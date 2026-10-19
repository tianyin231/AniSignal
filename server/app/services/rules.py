"""规则引擎：采集命中入库后、通知分发前执行「条件 → 动作」。

设计借鉴 Inoreader Rules（自写实现）：
- 条件 = 关键词布尔式（Mention 风格，见 services.boolean_query）+ 来源平台/分区 +
  重要性档（复用批次 1 的 intent_tier 三档）；
- 动作 = 打标签 / 转为事件 / 选择通知渠道（复用 webhook、邮件、QQ 机器人既有渠道）/
  标记已读；
- 规则按 priority 依次执行，单条规则失败只跳过它自己（fail-open），绝不阻塞采集
  与后续规则。

规则是站点级的：由管理端维护，命中台账（RuleHit）可追溯"这条内容为什么被处理"。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import (
    ContentVersion,
    Evidence,
    NotificationDelivery,
    RawContent,
    Rule,
    RuleHit,
    Source,
    User,
    WebhookConfig,
    WebhookDelivery,
)
from app.models.rules import (
    RULE_ACTION_EVENT,
    RULE_ACTION_NOTIFY,
    RULE_ACTION_READ,
    RULE_ACTION_TAG,
    RULE_CHANNEL_EMAIL,
    RULE_CHANNEL_QQ,
    RULE_CHANNEL_WEBHOOK,
)
from app.services import boolean_query, source_tier
from app.services.settings import get_rules_config

logger = logging.getLogger(__name__)

# 单条内容最多保留的规则标签
MAX_TAGS = 8
# 通知正文截断
NOTICE_LIMIT = 400
# 单条规则命中一次最多发多少封邮件。站内用户量不可控，而发信是同步 SMTP（每次
# 新建连接），全站逐封发会把采集进程连同一个 platform_slot 一起堵死（PERF-012）。
# 超出部分本轮跳过并记 warning：这是止血，彻底解法是把投递挪进异步队列。
MAX_EMAIL_RECIPIENTS = 50


@dataclass
class RuleRunStats:
    """一轮规则执行的结果计数（采集日志与管理端展示用）。"""

    contents: int = 0
    matched: int = 0
    actions: int = 0
    events: int = 0
    notified: int = 0
    errors: int = 0


@dataclass
class _Context:
    """一条待判定内容的最小上下文（标题/正文拼成布尔匹配的文本池）。"""

    content: RawContent
    title: str
    text: str
    source_name: str | None
    tier: str = source_tier.TIER_UNKNOWN

    @property
    def haystack(self) -> str:
        parts = [self.title, self.text, self.source_name or "", self.content.url or ""]
        parts.extend(str(key) for key in (self.content.matched_keywords or []))
        return " ".join(part for part in parts if part)

    @property
    def intent_tier(self) -> str:
        return self.content.intent_tier or ""

    @property
    def tier_label(self) -> str:
        return source_tier.label(self.tier)

    @property
    def link(self) -> str:
        return self.content.url or ""


@dataclass
class _Match:
    """一条命中的（规则, 内容）组合。"""

    rule: Rule
    ctx: _Context
    reason: str
    actions: list[dict] = field(default_factory=list)


async def load_enabled_rules(session: AsyncSession) -> list[Rule]:
    """启用中的规则，按 priority、id 升序（数值越小越先执行）。"""
    rows = (
        await session.execute(
            select(Rule)
            .where(Rule.enabled.is_(True))
            .order_by(Rule.priority.asc(), Rule.id.asc())
        )
    ).scalars()
    return list(rows)


def _normalized_actions(raw) -> list[dict]:
    """清洗动作列表：只认已知类型，未知类型忽略（不让脏配置炸掉整轮）。"""
    known = (RULE_ACTION_TAG, RULE_ACTION_EVENT, RULE_ACTION_NOTIFY, RULE_ACTION_READ)
    actions = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip() not in known:
            continue
        actions.append(item)
    return actions


def _normalized_channels(action: dict) -> list[str]:
    channels = action.get("channels")
    if isinstance(channels, str):
        channels = [channels]
    allowed = (RULE_CHANNEL_WEBHOOK, RULE_CHANNEL_EMAIL, RULE_CHANNEL_QQ)
    picked = [
        str(channel).strip()
        for channel in (channels or [])
        if str(channel).strip() in allowed
    ]
    # 没选渠道时退回机器人直推（批次 1 已有链路），规则不至于静默失效
    return list(dict.fromkeys(picked)) or [RULE_CHANNEL_QQ]


def _has_action(rule: Rule, kind: str) -> bool:
    return any(
        str(action.get("type") or "") == kind
        for action in _normalized_actions(rule.actions)
    )


def _condition_reason(rule: Rule, ctx: _Context, parsed) -> tuple[bool, str]:
    """条件判定；返回 (是否命中, 依据说明)。"""
    platforms = [str(item) for item in (rule.platforms or []) if item]
    if platforms and str(ctx.content.platform) not in platforms:
        return False, "平台不符"
    communities = [str(item) for item in (rule.communities or []) if item]
    if communities and str(ctx.content.community) not in communities:
        return False, "分区不符"
    tiers = [str(item) for item in (rule.intent_tiers or []) if item]
    if tiers and ctx.intent_tier not in tiers:
        return False, "重要性档不符"
    if not boolean_query.evaluate(parsed, ctx.haystack):
        return False, "关键词不符"
    return True, f"命中 {boolean_query.describe(parsed)}"


async def _load_contexts(
    session: AsyncSession, content_ids: list[int], cfg: dict | None = None
) -> list[_Context]:
    """取内容 + 最新版本标题正文 + 来源（供匹配与通知渲染）。

    先按当前阈值重算本批来源的档位（信源分级的落地点），再读档位，通知里的权重标注
    才不会停留在"未分级"。
    """
    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .where(ContentVersion.raw_content_id.in_(content_ids))
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    source_ids = [
        int(value)
        for value in (
            await session.execute(
                select(RawContent.source_id).where(RawContent.id.in_(content_ids))
            )
        ).scalars()
        if value
    ]
    if source_ids:
        try:
            await source_tier.refresh_source_tiers(
                session, source_ids, (cfg or {}).get("source_tiers")
            )
        except Exception as exc:  # noqa: BLE001 - 分级失败不该阻断规则执行
            logger.warning("source tier refresh failed: %r", exc)
    rows = await session.execute(
        select(RawContent, ContentVersion.title, ContentVersion.text, Source)
        .join(latest_sq, latest_sq.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest_sq.c.rid)
            & (ContentVersion.version == latest_sq.c.version),
        )
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(RawContent.id.in_(content_ids))
        .order_by(RawContent.id)
    )
    return [
        _Context(
            content=content,
            title=str(title or content.external_id or ""),
            text=str(text or ""),
            source_name=source.name if source else None,
            tier=str(source.tier) if source and source.tier else source_tier.TIER_UNKNOWN,
        )
        for content, title, text, source in rows
    ]


async def apply_rules(
    session: AsyncSession,
    content_ids: list[int],
    *,
    config: dict | None = None,
) -> RuleRunStats:
    """对一批入库命中执行规则；返回计数。任何单条规则失败都不影响其它规则。"""
    stats = RuleRunStats()
    ids = [int(value) for value in dict.fromkeys(content_ids or []) if value]
    if not ids:
        return stats
    cfg = config or await get_rules_config(session)
    if not cfg.get("enabled", True):
        return stats
    rules = await load_enabled_rules(session)
    if not rules:
        return stats
    contexts = await _load_contexts(session, ids, cfg)
    stats.contents = len(contexts)
    if not contexts:
        return stats

    # 已成功记账的（规则, 内容）组合不重复执行；失败的下轮重试（fail-open 但可恢复）
    done = set(
        (
            await session.execute(
                select(RuleHit.rule_id, RuleHit.content_id).where(
                    RuleHit.content_id.in_(ids), RuleHit.error.is_(None)
                )
            )
        ).all()
    )

    matches: list[_Match] = []
    for ctx in contexts:
        for rule in rules:
            if (rule.id, ctx.content.id) in done:
                continue
            try:
                parsed = boolean_query.parse(rule.keyword_expr)
                matched, reason = _condition_reason(rule, ctx, parsed)
            except Exception as exc:  # noqa: BLE001 - 单条规则条件失败不影响其它规则
                stats.errors += 1
                rule.last_error = f"条件解析失败：{exc!s}"[:300]
                logger.warning("rule %s condition failed: %r", rule.id, exc)
                continue
            if matched:
                matches.append(
                    _Match(rule=rule, ctx=ctx, reason=reason,
                           actions=_normalized_actions(rule.actions))
                )

    # 转事件动作统一跑一次聚合（ensure 语义，幂等）
    promotable = [match.ctx for match in matches if _has_action(match.rule, RULE_ACTION_EVENT)]
    event_map = await _promote_to_events(session, promotable) if promotable else {}
    stats.events = len(event_map)

    for match in matches:
        try:
            applied, notes = await _execute_actions(
                session, match.rule, match.ctx, match.actions, cfg, event_map, stats
            )
        except Exception as exc:  # noqa: BLE001 - fail-open：跳过这条规则，继续下一条
            stats.errors += 1
            match.rule.last_error = f"动作执行失败：{exc!s}"[:300]
            logger.warning("rule %s actions failed: %r", match.rule.id, exc)
            await _record_failure(session, match, exc)
            continue

        match.rule.hit_count = int(match.rule.hit_count or 0) + 1
        match.rule.last_hit_at = utcnow()
        match.rule.last_error = None
        await _record_hit(
            session,
            match,
            actions=applied,
            detail="；".join(notes)[:500],
            error=None,
        )
        stats.matched += 1
        stats.actions += len(applied)

    await session.commit()
    return stats


async def _record_hit(
    session: AsyncSession,
    match: _Match,
    *,
    actions: list[dict],
    detail: str,
    error: str | None,
) -> None:
    """记账（一条规则对一条内容一行）。

    失败也记账便于管理端排查，但保留 error 字段：下一轮只跳过 error 为空的组合，
    因此失败的组合会自动重试，成功的不会重复执行。
    """
    row = await session.scalar(
        select(RuleHit).where(
            RuleHit.rule_id == match.rule.id,
            RuleHit.content_id == match.ctx.content.id,
        )
    )
    if row is None:
        session.add(
            RuleHit(
                rule_id=match.rule.id,
                content_id=match.ctx.content.id,
                actions=actions,
                detail=detail,
                error=error,
            )
        )
        return
    row.actions = actions
    row.detail = detail
    row.error = error


async def _record_failure(session: AsyncSession, match: _Match, exc: Exception) -> None:
    await _record_hit(
        session,
        match,
        actions=[],
        detail=match.reason,
        error=f"{type(exc).__name__}: {exc!s}"[:300],
    )


async def _execute_actions(
    session: AsyncSession,
    rule: Rule,
    ctx: _Context,
    actions: list[dict],
    cfg: dict,
    event_map: dict[int, int],
    stats: RuleRunStats,
) -> tuple[list[dict], list[str]]:
    applied: list[dict] = []
    notes: list[str] = []
    for action in actions:
        kind = str(action.get("type") or "")
        if kind == RULE_ACTION_TAG:
            value = str(action.get("value") or "").strip()[:32]
            if not value:
                continue
            tags = [str(tag) for tag in (ctx.content.rule_tags or [])]
            if value not in tags:
                tags.append(value)
                ctx.content.rule_tags = tags[:MAX_TAGS]
            applied.append({"type": kind, "value": value})
            notes.append(f"打标签 {value}")
        elif kind == RULE_ACTION_EVENT:
            event_id = event_map.get(ctx.content.id)
            applied.append({"type": kind})
            notes.append(f"转为事件 #{event_id}" if event_id else "未归入事件")
        elif kind == RULE_ACTION_READ:
            if not ctx.content.rule_acked:
                ctx.content.rule_acked = True
                ctx.content.rule_acked_at = utcnow()
            applied.append({"type": kind})
            notes.append("标记已读")
        elif kind == RULE_ACTION_NOTIFY:
            channels = _normalized_channels(action)
            outcome = await _notify(session, rule, ctx, channels)
            applied.append({"type": kind, "channels": channels})
            stats.notified += sum(outcome.values())
            notes.append("推送 " + " ".join(f"{key}={value}" for key, value in outcome.items()))
    return applied, notes


async def _notify(
    session: AsyncSession, rule: Rule, ctx: _Context, channels: list[str]
) -> dict[str, int]:
    """按规则选定的渠道推送；各渠道独立失败互不影响（fail-open）。"""
    outcome: dict[str, int] = {}
    if RULE_CHANNEL_QQ in channels:
        try:
            from app.services.bot import push_important_contents

            outcome["qq"] = await push_important_contents(session, [ctx.content.id])
        except Exception as exc:  # noqa: BLE001 - 单渠道失败不阻塞其它渠道
            logger.warning("rule %s qq push failed: %r", rule.id, exc)
            outcome["qq"] = 0
    if RULE_CHANNEL_WEBHOOK in channels:
        try:
            outcome["webhook"] = await _push_webhooks(session, rule, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rule %s webhook push failed: %r", rule.id, exc)
            outcome["webhook"] = 0
    if RULE_CHANNEL_EMAIL in channels:
        try:
            outcome["email"] = await _push_emails(session, rule, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rule %s email push failed: %r", rule.id, exc)
            outcome["email"] = 0
    return outcome


def _notice_item(ctx: _Context) -> dict:
    """通知用的内容视图：标题带来源权重标注（信源分级的出口之一）。"""
    return {
        "id": ctx.content.id,
        "title": f"[{ctx.tier_label}] {ctx.title}"[:255],
        "url": ctx.link,
        "summary": (ctx.text or "")[:NOTICE_LIMIT],
        "source": ctx.source_name or str(ctx.content.platform),
        "platform": str(ctx.content.platform),
        "community": str(ctx.content.community),
        "keywords": list(ctx.content.matched_keywords or []),
        "tier": ctx.intent_tier or "normal",
        "source_tier": ctx.tier_label,
        "published_at": ctx.content.published_at or ctx.content.collected_at,
    }


async def _push_webhooks(session: AsyncSession, rule: Rule, ctx: _Context) -> int:
    """推送给所有启用中的 webhook（按 tier_filter 过滤），按规则+内容去重。"""
    from app.services.webhooks import MAX_ATTEMPTS, build_payload, deliver_webhook, tier_allowed

    hooks = list(
        (
            await session.execute(
                select(WebhookConfig)
                .join(User, User.id == WebhookConfig.user_id)
                .where(WebhookConfig.enabled.is_(True), User.status == "active")
                .order_by(WebhookConfig.id)
            )
        ).scalars()
    )
    if not hooks:
        return 0
    item = _notice_item(ctx)
    sent = 0
    for hook in hooks:
        if not tier_allowed(hook, item):
            continue
        dedupe_key = f"rule:{rule.id}:content:{ctx.content.id}:webhook:{hook.id}"
        existing = await session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.dedupe_key == dedupe_key)
        )
        if existing is not None and (
            existing.status == "sent" or existing.attempts >= MAX_ATTEMPTS
        ):
            continue
        row = await deliver_webhook(
            session,
            hook,
            build_payload(item),
            kind="rule",
            content_id=ctx.content.id,
            dedupe_key=dedupe_key,
            row=existing,
        )
        sent += int(row.status == "sent")
    return sent


async def _push_emails(session: AsyncSession, rule: Rule, ctx: _Context) -> int:
    """给开启了邮件通知的已验证活跃用户各发一封（按规则+内容+用户去重）。

    性能边界（PERF-012）：原来是「每用户 1 次去重查询 + 1 次 INSERT/COMMIT/REFRESH +
    1 次 SMTP」，500 用户 × 10 内容就是 3 万次查询和 5 千次 SMTP 握手，全同步堵在采集
    任务里。现在收件人截断到 MAX_EMAIL_RECIPIENTS，去重键一次性批量查，投递行批量
    flush 后统一发信——查询次数从 O(用户数) 降到常数。
    """
    from app.services.notifications import beijing_today, send_admin_email

    users = list(
        (
            await session.execute(
                select(User)
                .where(
                    User.notification_enabled.is_(True),
                    User.is_verified.is_(True),
                    User.status == "active",
                )
                .order_by(User.id)
                .limit(MAX_EMAIL_RECIPIENTS)
            )
        ).scalars()
    )
    if not users:
        return 0
    subject = f"[AniSignal 规则] {rule.name}"[:255]
    body = _render_notice(rule, ctx)
    keys = {
        user.id: f"rule:{rule.id}:content:{ctx.content.id}:email:{user.id}" for user in users
    }
    # 一次把已有的去重键全捞回来，避免每个用户一次查询
    existing = set(
        (
            await session.execute(
                select(NotificationDelivery.dedupe_key).where(
                    NotificationDelivery.dedupe_key.in_(list(keys.values()))
                )
            )
        ).scalars()
    )
    fresh = [user for user in users if keys[user.id] not in existing]
    if not fresh:
        return 0
    if len(fresh) == MAX_EMAIL_RECIPIENTS:
        logger.warning(
            "rule %s email recipients truncated at %s for content %s",
            rule.id, MAX_EMAIL_RECIPIENTS, ctx.content.id,
        )
    # 批量建行：flush 拿 id 但不逐个 commit（send_admin_email 内部会各自提交）
    rows = [
        NotificationDelivery(
            user_id=user.id,
            kind="broadcast",
            source="rules",
            period_date=beijing_today(),
            dedupe_key=keys[user.id],
            status="pending",
            subject=subject,
            body=body,
            matched_keywords=[],
            content_ids=[],
        )
        for user in fresh
    ]
    session.add_all(rows)
    await session.flush()
    sent = 0
    for row in rows:
        result = await send_admin_email(session, row.id)
        sent += int(result.get("status") == "sent")
    return sent


def _render_notice(rule: Rule, ctx: _Context) -> str:
    """通知正文：命中内容 + 来源权重 + 命中依据。"""
    lines = [
        f"规则「{rule.name}」命中一条内容：",
        "",
        f"标题：{ctx.title}",
        f"来源：{ctx.source_name or '未知'}（{ctx.tier_label}）",
        f"平台：{ctx.content.platform}　分区：{ctx.content.community}",
        f"重要性：{ctx.intent_tier or '未打标'}",
    ]
    if ctx.link:
        lines.append(f"链接：{ctx.link}")
    if rule.note:
        lines.append(f"规则说明：{rule.note}")
    return "\n".join(lines)[:2000]


async def _promote_to_events(
    session: AsyncSession, contexts: list[_Context]
) -> dict[int, int]:
    """把命中内容送进事件聚合（ensure 语义），返回 content_id → event_id。"""
    if not contexts:
        return {}
    from app.services.events import run_aggregation

    await run_aggregation(session)
    await session.commit()
    ids = [ctx.content.id for ctx in contexts]
    evidence = await session.execute(
        select(Evidence.raw_content_id, Evidence.event_id).where(
            Evidence.raw_content_id.in_(ids),
            Evidence.role != "excluded",
        )
    )
    mapping: dict[int, int] = {}
    for raw_id, event_id in evidence:
        mapping.setdefault(raw_id, event_id)
    return mapping
