"""每日情报简报（批次 3c）：定时生成昨日回顾，AI 写成一篇可读日报。

思路借鉴 Readwise Reader 的每日摘要 + Brand24 的每日洞察（均为自写实现）：
- 四个固定分节：昨日事件汇总 / 重要命中 Top / 商品进展 / 声量回顾；
- 先结构化采集（确定性，可复算），再交给 AI 写成摘要；AI 不可用时退回模板文，
  简报本身永远能生成——「没有简报」比「简报难看」更糟；
- 推送复用既有渠道：邮件（NotificationDelivery 落库可查）+ QQ 机器人（critical 穿透静默）。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import DailyBrief, Event, Evidence, Product, RawContent
from app.models.content import ContentVersion

logger = logging.getLogger(__name__)

# 每个分节最多列几条（简报是给人读的，不是数据导出）
MAX_SECTION_ITEMS = 8
# 重要命中 Top 的取数上限
MAX_TOP_HITS = 5
# AI 摘要字数上限
MAX_SUMMARY_CHARS = 600

_BRIEF_SYSTEM = (
    "你是站点情报编辑。根据给定的昨日结构化数据写一段中文每日简报导读，"
    "200 字以内，说清「昨天发生了什么、哪件事最值得看」。"
    "只能使用给定数据里的事实，禁止补充数据中没有的游戏名、数字、时间或结论。"
    "只输出 JSON：{\"summary\": \"...\"}。数据不足时如实说明昨天信息量少。"
)


# ---------- 数据采集 ----------


def _day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _iso(value: datetime | None) -> str | None:
    """分节要整块塞进 JSON 列，datetime 一律转 ISO 字符串（naive UTC）。"""
    return None if value is None else value.isoformat(timespec="seconds")


def _brief_day(now: datetime) -> date:
    """简报口径：北京时间「昨天」——早 8 点后生成时覆盖的是前一日。"""
    local = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    return (local - timedelta(days=1)).date()


async def _events_section(session: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    rows = (
        await session.execute(
            select(Event, func.count(Evidence.id).label("evidence_count"))
            .join(Evidence, Evidence.event_id == Event.id, isouter=True)
            .where(
                Event.first_seen_at >= start,
                Event.first_seen_at < end,
                Event.is_active.is_(True),
            )
            .group_by(Event.id)
            .order_by(Event.importance.desc(), Event.last_updated_at.desc(), Event.id.desc())
            .limit(MAX_SECTION_ITEMS)
        )
    ).all()
    return [
        {
            "event_id": event.id,
            "title": event.title,
            "category": event.category,
            "identity": event.identity,
            "importance": event.importance,
            "confidence": event.confidence,
            "summary": (event.summary or "")[:200] or None,
            "evidence_count": int(count or 0),
            "first_seen_at": _iso(event.first_seen_at),
        }
        for event, count in rows
    ]


async def _top_hits_section(session: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    """昨日重要命中 Top：AI 意图打标为 important 的采集内容（依赖批次 1）。"""
    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(RawContent, ContentVersion)
            .join(latest_sq, latest_sq.c.rid == RawContent.id)
            .join(
                ContentVersion,
                (ContentVersion.raw_content_id == latest_sq.c.rid)
                & (ContentVersion.version == latest_sq.c.version),
            )
            .where(
                RawContent.hidden.is_(False),
                RawContent.intent_tier == "important",
                RawContent.collected_at >= start,
                RawContent.collected_at < end,
            )
            .order_by(RawContent.collected_at.desc(), RawContent.id.desc())
            .limit(MAX_TOP_HITS)
        )
    ).all()
    return [
        {
            "content_id": raw.id,
            "title": (version.title or raw.external_id)[:160],
            "platform": raw.platform,
            "url": raw.url,
            "published_at": _iso(raw.published_at),
            "collected_at": _iso(raw.collected_at),
            "matched_keywords": list(raw.matched_keywords or [])[:5],
            "intent_reason": raw.intent_reason,
        }
        for raw, version in rows
    ]


async def _products_section(session: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    """商品进展：昨日有新证据的商品 + 当前价格观测 + 履约阶段。"""
    rows = (
        await session.execute(
            select(
                Product,
                func.count(Evidence.id).label("evidence_count"),
                func.max(func.coalesce(RawContent.published_at, RawContent.collected_at)).label("latest_at"),
            )
            .join(Event, Event.product_id == Product.id)
            .join(Evidence, Evidence.event_id == Event.id)
            .join(RawContent, RawContent.id == Evidence.raw_content_id)
            .where(
                Event.product_id.is_not(None),
                RawContent.hidden.is_(False),
                RawContent.collected_at >= start,
                RawContent.collected_at < end,
            )
            .group_by(Product.id)
            .order_by(func.count(Evidence.id).desc(), Product.id)
            .limit(MAX_SECTION_ITEMS)
        )
    ).all()
    if not rows:
        return []
    from app.models import ProductProfile

    profiles = {
        profile.product_id: profile
        for profile in (
            await session.execute(
                select(ProductProfile).where(
                    ProductProfile.product_id.in_([product.id for product, _, _ in rows])
                )
            )
        ).scalars()
    }
    items = []
    for product, count, latest_at in rows:
        profile = profiles.get(product.id)
        items.append(
            {
                "product_id": product.id,
                "slug": product.slug,
                "name": product.name,
                "new_evidence": int(count or 0),
                "latest_evidence_at": _iso(latest_at),
                "stage": product.computed_stage,
                "price_current": profile.price_current if profile else None,
                "price_observed_at": profile.price_observed_at if profile else None,
            }
        )
    return items


async def _volume_section(session: AsyncSession, start: datetime, end: datetime) -> dict:
    """声量回顾：昨日命中总数、平台分布与关键词 Top。

    已标记 ignore 的命中不计入——用户明确说过那是噪音（与机器人推送同一口径）。
    """
    window = (
        RawContent.hidden.is_(False),
        RawContent.collected_at >= start,
        RawContent.collected_at < end,
        # 未打标（NULL）要算进来：SQL 里 NULL != 'ignore' 是 NULL，得显式放行
        or_(RawContent.intent_tier.is_(None), RawContent.intent_tier != "ignore"),
    )
    total = (
        await session.scalar(select(func.count(RawContent.id)).where(*window)) or 0
    )
    by_platform = dict(
        (
            await session.execute(
                select(RawContent.platform, func.count(RawContent.id))
                .where(*window)
                .group_by(RawContent.platform)
            )
        ).all()
    )
    keywords: dict[str, int] = {}
    matched_rows = (
        (
            await session.execute(
                select(RawContent.matched_keywords).where(*window)
            )
        )
        .scalars()
        .all()
    )
    for matched in matched_rows:
        for keyword in matched or []:
            key = str(keyword).strip()
            if key:
                keywords[key] = keywords.get(key, 0) + 1
    top_keywords = sorted(keywords.items(), key=lambda item: (-item[1], item[0]))[:MAX_TOP_HITS]
    return {
        "total": int(total),
        "by_platform": {str(k): int(v) for k, v in by_platform.items()},
        "top_keywords": [{"keyword": k, "count": v} for k, v in top_keywords],
    }


async def collect_sections(session: AsyncSession, day: date) -> dict:
    """采集四个分节（确定性，可重复执行得到同样结果）。"""
    start, end = _day_window(day)
    return {
        "events": await _events_section(session, start, end),
        "top_hits": await _top_hits_section(session, start, end),
        "products": await _products_section(session, start, end),
        "volume": await _volume_section(session, start, end),
    }


# ---------- AI 写成简报 ----------


def _fallback_summary(day: date, sections: dict) -> str:
    events = sections.get("events") or []
    hits = sections.get("top_hits") or []
    products = sections.get("products") or []
    volume = sections.get("volume") or {}
    parts: list[str] = [f"{day:%m 月 %d 日}全站新增 {volume.get('total', 0)} 条命中。"]
    if events:
        parts.append(f"新增事件 {len(events)} 起，最值得关注的是「{events[0]['title']}」。")
    else:
        parts.append("昨日没有新增归并事件。")
    if hits:
        parts.append(f"重要命中 {len(hits)} 条，首条为「{hits[0]['title']}」。")
    if products:
        parts.append(f"{len(products)} 个商品有新进展。")
    return "".join(parts)


def _facts_blob(day: date, sections: dict) -> str:
    """把结构化数据压成一段给 AI 的上下文（数字必须原样出现，便于校验）。"""
    lines = [f"简报日期：{day.isoformat()}"]
    volume = sections.get("volume") or {}
    lines.append(f"昨日命中总数：{volume.get('total', 0)}")
    if volume.get("by_platform"):
        lines.append(
            "平台分布：" + "、".join(f"{k} {v} 条" for k, v in volume["by_platform"].items())
        )
    for keyword in volume.get("top_keywords") or []:
        lines.append(f"关键词 {keyword['keyword']}：{keyword['count']} 条")
    for index, event in enumerate(sections.get("events") or [], start=1):
        lines.append(
            f"事件{index}：{event['title']}（类目 {event['category']}，"
            f"{'重要' if event['importance'] else '一般'}，证据 {event['evidence_count']} 条）"
        )
        if event.get("summary"):
            lines.append(f"  摘要：{event['summary']}")
    for index, hit in enumerate(sections.get("top_hits") or [], start=1):
        lines.append(f"重要命中{index}：{hit['title']}（{hit['platform']}）")
    for index, product in enumerate(sections.get("products") or [], start=1):
        lines.append(
            f"商品{index}：{product['name']}，昨日新增证据 {product['new_evidence']} 条，"
            f"履约阶段 {product['stage']}"
        )
    return "\n".join(lines)


def _summary_supported(summary: str, facts: str) -> bool:
    """拒绝事实外数字：AI 简报里出现的每个数字都应能在结构化数据里找到。"""
    for number in re.findall(r"\d+(?:\.\d+)?", summary):
        if number not in facts:
            return False
    return True


async def _compose_summary(session: AsyncSession, day: date, sections: dict) -> tuple[str, str, str | None]:
    """返回 (摘要, ai_status, model)。AI 不可用/失败/编造数字时都退回模板文。"""
    from app.services.ai import ai_available, chat, get_ai_config, record_usage

    fallback = _fallback_summary(day, sections)
    config = await get_ai_config(session)
    if not ai_available(config):
        return fallback, "fallback", None
    facts = _facts_blob(day, sections)
    started = time.monotonic()
    try:
        out, usage = await chat(config, _BRIEF_SYSTEM, facts, json_mode=True)
        parsed = json.loads(out)
        summary = str(parsed.get("summary") or "").strip()[:MAX_SUMMARY_CHARS]
        await record_usage(
            session,
            task="daily_brief",
            config=config,
            usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - 简报必须能生成，AI 失败只降级
        logger.warning("daily brief AI compose failed for %s: %r", day, exc)
        return fallback, "fallback", None
    if not summary or not _summary_supported(summary, facts):
        return fallback, "fallback", None
    return summary, "ai", config.get("model")


# ---------- 生成与推送 ----------


def _title(day: date) -> str:
    return f"AniSignal 每日情报简报 · {day:%Y 年 %m 月 %d 日}"


async def generate_brief(
    session: AsyncSession, day: date | None = None, *, force: bool = False
) -> tuple[DailyBrief, bool]:
    """生成某一天的简报。返回 (brief, created)。

    默认不覆盖已存在的同日期简报（重复跑安全）；force=True 时重算内容。
    """
    now = utcnow()
    day = day or _brief_day(now)
    existing = await session.scalar(select(DailyBrief).where(DailyBrief.brief_date == day))
    if existing is not None and not force:
        return existing, False
    sections = await collect_sections(session, day)
    summary, ai_status, model = await _compose_summary(session, day, sections)
    stats = {
        "events": len(sections["events"]),
        "top_hits": len(sections["top_hits"]),
        "products": len(sections["products"]),
        "volume_total": int((sections["volume"] or {}).get("total", 0)),
        "important_events": sum(1 for event in sections["events"] if event["importance"]),
    }
    if existing is None:
        brief = DailyBrief(
            brief_date=day,
            title=_title(day),
            summary=summary,
            sections=sections,
            stats=stats,
            status="ready",
            ai_status=ai_status,
            model=model,
        )
        session.add(brief)
    else:
        existing.title = _title(day)
        existing.summary = summary
        existing.sections = sections
        existing.stats = stats
        existing.ai_status = ai_status
        existing.model = model
        existing.error = None
        existing.status = "ready"
        brief = existing
    await session.commit()
    await session.refresh(brief)
    return brief, existing is None


def render_brief_text(brief: DailyBrief) -> tuple[str, str]:
    """把简报渲染成邮件标题与纯文本正文（管理端预览与邮件共用）。"""
    sections = brief.sections or {}
    lines: list[str] = [brief.title, ""]
    if brief.summary:
        lines.extend([brief.summary, ""])
    volume = sections.get("volume") or {}
    if volume:
        lines.append(f"▎声量回顾：昨日命中 {volume.get('total', 0)} 条")
        for keyword in (volume.get("top_keywords") or [])[:5]:
            lines.append(f"  · {keyword['keyword']} {keyword['count']} 条")
        lines.append("")
    events = sections.get("events") or []
    if events:
        lines.append("▎昨日事件汇总")
        for event in events[:MAX_SECTION_ITEMS]:
            mark = "★" if event.get("importance") else "·"
            lines.append(f"  {mark} {event['title']}（证据 {event.get('evidence_count', 0)} 条）")
            if event.get("summary"):
                lines.append(f"      {event['summary']}")
        lines.append("")
    hits = sections.get("top_hits") or []
    if hits:
        lines.append("▎重要命中 Top")
        for index, hit in enumerate(hits[:MAX_TOP_HITS], start=1):
            lines.append(f"  {index}. {hit['title']}（{hit.get('platform', '')}）")
        lines.append("")
    products = sections.get("products") or []
    if products:
        lines.append("▎商品进展")
        for product in products[:MAX_SECTION_ITEMS]:
            lines.append(f"  · {product['name']}：昨日新增证据 {product.get('new_evidence', 0)} 条")
        lines.append("")
    return brief.title, "\n".join(lines).strip()


async def _deliver_email(session: AsyncSession, brief: DailyBrief) -> int:
    """给启用通知的已验证用户发简报邮件，并在 NotificationDelivery 落库。"""
    from app.models import NotificationDelivery, User
    from app.services.email_templates import notice_email
    from app.services.mailer import send_email
    from app.services.settings import get_effective_smtp

    users = list(
        (
            await session.execute(
                select(User).where(
                    User.notification_enabled.is_(True),
                    User.is_verified.is_(True),
                    User.status == "active",
                )
            )
        ).scalars()
    )
    if not users:
        return 0
    subject, body = render_brief_text(brief)
    html = notice_email(subject, body, preheader=f"{brief.brief_date:%m 月 %d 日}情报简报")
    smtp = await get_effective_smtp(session)
    sent = 0
    for user in users:
        dedupe_key = f"brief:{brief.id}:{user.id}"
        if (
            await session.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.dedupe_key == dedupe_key
                )
            )
            is not None
        ):
            continue
        delivery = NotificationDelivery(
            user_id=user.id,
            kind="brief",
            source="schedule",
            period_date=brief.brief_date,
            dedupe_key=dedupe_key,
            status="pending",
            subject=subject,
            body=body,
            html_body=html,
        )
        session.add(delivery)
        await session.commit()
        ok = await send_email(user.email, subject, body, smtp=smtp, html_body=html)
        delivery.status = "sent" if ok else "failed"
        delivery.error = None if ok else "SMTP 发送失败，请查看服务端日志"
        delivery.sent_at = utcnow() if ok else None
        await session.commit()
        sent += int(ok)
    return sent


async def _deliver_qq(session: AsyncSession, brief: DailyBrief) -> int:
    """推给每个启用的机器人目标（critical 穿透静默时段， dedupe key 按简报唯一）。"""
    from app.models import BotTarget
    from app.services.bot import create_delivery

    targets = list(
        (await session.execute(select(BotTarget).where(BotTarget.enabled.is_(True)))).scalars()
    )
    if not targets:
        return 0
    subject, body = render_brief_text(brief)
    for target in targets:
        await create_delivery(session, target, "critical", f"{subject}\n\n{body}", f"brief:{brief.id}")
    return len(targets)


async def deliver_brief(session: AsyncSession, brief: DailyBrief) -> dict:
    """推送一份简报：邮件 + QQ；结果写回 brief.delivered 便于管理端核对。"""
    try:
        emails = await _deliver_email(session, brief)
    except Exception as exc:  # noqa: BLE001 - 单渠道失败不影响另一渠道
        logger.warning("brief %s email delivery failed: %r", brief.id, exc)
        emails = 0
    try:
        qq = await _deliver_qq(session, brief)
    except Exception as exc:  # noqa: BLE001
        logger.warning("brief %s qq delivery failed: %r", brief.id, exc)
        qq = 0
    brief.delivered = {"email": emails, "qq": qq, "at": utcnow().isoformat()}
    brief.status = "sent" if (emails or qq) else "failed"
    brief.error = None if (emails or qq) else "没有可用渠道：未配置收件人或未启用机器人目标"
    brief.sent_at = utcnow()
    await session.commit()
    await session.refresh(brief)
    return {"email": emails, "qq": qq, "status": brief.status}


async def run_daily_brief(session: AsyncSession, now: datetime | None = None) -> dict:
    """定时入口：生成昨日简报并推送（同一天重复跑不会重复发）。"""
    now = now or utcnow()
    day = _brief_day(now)
    brief, created = await generate_brief(session, day)
    delivered = await deliver_brief(session, brief)
    logger.info("daily brief %s (%s) created=%s delivered=%s", brief.id, day, created, delivered)
    return {
        "brief_id": brief.id,
        "date": day.isoformat(),
        "created": created,
        "ai_status": brief.ai_status,
        **delivered,
    }


# ---------- 管理端查询 ----------


def _view(brief: DailyBrief) -> dict:
    return {
        "id": brief.id,
        "brief_date": brief.brief_date,
        "title": brief.title,
        "summary": brief.summary,
        "sections": brief.sections or {},
        "stats": brief.stats or {},
        "status": brief.status,
        "ai_status": brief.ai_status,
        "model": brief.model,
        "error": brief.error,
        "delivered": brief.delivered or {},
        "sent_at": brief.sent_at,
        "created_at": brief.created_at,
    }


async def list_briefs(
    session: AsyncSession, page: int = 1, page_size: int = 20, day: date | None = None
) -> dict:
    conditions = []
    if day is not None:
        conditions.append(DailyBrief.brief_date == day)
    total = await session.scalar(select(func.count(DailyBrief.id)).where(*conditions)) or 0
    rows = list(
        (
            await session.execute(
                select(DailyBrief)
                .where(*conditions)
                .order_by(DailyBrief.brief_date.desc(), DailyBrief.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
    )
    return {
        "items": [_view(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_brief(session: AsyncSession, brief_id: int) -> DailyBrief | None:
    return await session.get(DailyBrief, brief_id)


async def preview_brief(session: AsyncSession, day: date | None = None) -> dict:
    """管理端试跑：算出分节与摘要但不落库、不推送。"""
    now = utcnow()
    day = day or _brief_day(now)
    sections = await collect_sections(session, day)
    summary, ai_status, model = await _compose_summary(session, day, sections)
    return {
        "brief_date": day.isoformat(),
        "title": _title(day),
        "summary": summary,
        "sections": sections,
        "ai_status": ai_status,
        "model": model,
        "stored": await session.scalar(
            select(func.count(DailyBrief.id)).where(DailyBrief.brief_date == day)
        )
        or 0,
    }
