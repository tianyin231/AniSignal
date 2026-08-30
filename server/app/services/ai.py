"""AI 层：OpenAI 兼容接口的轻封装 + 事件摘要/内容翻译任务 + 用量记账。

配置来源：site_settings('ai') 优先，环境变量（ANISIGNAL_AI_*）兜底；
未配置时所有任务安全跳过（框架就绪，配好 key 即生效）。
定价（每百万 token 单价）在 ai 设置里配置，费用 = tokens × 单价 / 1e6。
"""

import json
import logging
import re
import time
from html import unescape

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import get_settings
from app.models import (
    AiUsage,
    ContentTranslation,
    ContentVersion,
    Event,
    EventRevision,
    Evidence,
    FULFILLMENT_STAGES,
    Product,
    RawContent,
    Source,
)

logger = logging.getLogger(__name__)


async def get_ai_config(session: AsyncSession) -> dict:
    from app.services.settings import get_setting

    env = get_settings()
    stored = (await get_setting(session, "ai")) or {}
    return {
        "base_url": (stored.get("base_url") or env.ai_base_url or "").rstrip("/"),
        "api_key": stored.get("api_key") or env.ai_api_key or "",
        "model": stored.get("model") or env.ai_model or "",
        "price_in": float(stored.get("price_in") or 0),  # 每百万输入 token 单价（元/美元由你定）
        "price_out": float(stored.get("price_out") or 0),
    }


def ai_available(config: dict) -> bool:
    return bool(config.get("base_url") and config.get("api_key") and config.get("model"))


async def record_usage(
    session: AsyncSession,
    *,
    task: str,
    config: dict,
    usage: dict | None,
    duration_ms: int,
    ref_id: int | None = None,
    status: str = "ok",
) -> None:
    prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((usage or {}).get("completion_tokens") or 0)
    cost = (prompt_tokens * config.get("price_in", 0) + completion_tokens * config.get("price_out", 0)) / 1e6
    session.add(
        AiUsage(
            task=task,
            ref_id=ref_id,
            model=config.get("model") or "",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=round(cost, 6),
            duration_ms=duration_ms,
            status=status,
        )
    )
    await session.commit()


async def chat(
    config: dict, system: str, user: "str | list", *, json_mode: bool = False
) -> tuple[str, dict | None]:
    """OpenAI 兼容 chat/completions；返回 (回复文本, usage)。

    user 为 str 时是纯文本；为 list 时是 OpenAI 多模态 content 分片
    （如 [{"type": "text", ...}, {"type": "image_url", ...}]），原样透传。
    """
    import asyncio

    body: dict = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{config['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"], data.get("usage")


def _public_view(config: dict) -> dict:
    return {
        "base_url": config["base_url"],
        "model": config["model"],
        "api_key_set": bool(config["api_key"]),
        "price_in": config.get("price_in", 0),
        "price_out": config.get("price_out", 0),
    }


# ---------- 任务实现（worker 与管理端共用） ----------


def _plain_evidence(text: str) -> str:
    import bleach

    plain = unescape(bleach.clean(text or "", tags=[], strip=True))
    return re.sub(r"\s+", " ", plain).strip()


def _summary_supported(summary: str, evidence_text: str) -> bool:
    """拒绝证据外的其它游戏名和数字事实；允许活动名标点归一化。"""
    other_games = (
        "战双帕弥什",
        "战双",
        "原神",
        "崩坏：星穹铁道",
        "崩坏星穹铁道",
        "绝区零",
    )
    for game_name in other_games:
        if game_name in summary and game_name not in evidence_text:
            return False
    for number in re.findall(r"\d+(?:\.\d+)?", summary):
        if number not in evidence_text:
            return False
    return True


def _fallback_summary(event: Event, evidence_count: int) -> str:
    if event.identity == "official":
        return f"官方发布「{event.title}」。当前收录 {evidence_count} 条来源证据，具体内容以原文为准。"
    return f"已收录与「{event.title}」相关的 {evidence_count} 条线索，结论以证据原文为准。"


async def summarize_event(
    session: AsyncSession, event_id: int, *, force: bool = False
) -> dict:
    """AI 只生成证据摘要；结构化事实始终由聚合引擎确定。"""
    event = await session.get(Event, event_id)
    if event is None:
        return {"status": "skipped", "reason": "event_not_found"}
    if event.manual_locked and not force:
        return {"status": "skipped", "reason": "manual_locked"}

    config = await get_ai_config(session)

    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )

    rows = await session.execute(
        select(RawContent, ContentVersion, Evidence)
        .join(Evidence, Evidence.raw_content_id == RawContent.id)
        .join(latest_sq, latest_sq.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest_sq.c.rid)
            & (ContentVersion.version == latest_sq.c.version),
        )
        .where(Evidence.event_id == event_id, Evidence.role != "excluded")
        .order_by(RawContent.published_at.asc())
        .limit(8)
    )
    evidences = []
    evidence_context = [event.title]
    for raw, version, _ in rows:
        title = (version.title or raw.external_id).strip()
        text = _plain_evidence(version.text or "")
        published = raw.published_at.strftime("%Y-%m-%d %H:%M") if raw.published_at else "时间未知"
        evidence_context.extend([title, text, published])
        evidences.append(
            f"- 证据#{raw.id} [{raw.platform}] {published} {title[:120]}：{text[:1600]}"
        )
    if not evidences:
        return {"status": "skipped", "reason": "no_evidences"}
    evidence_context.append(str(len(evidences)))

    used_fallback = not ai_available(config)
    if used_fallback:
        summary = _fallback_summary(event, len(evidences))
    else:
        system = (
            "你是库洛游戏《鸣潮》的情报编辑，只能根据给定证据输出 JSON："
            '{"summary": "100字以内中文结论摘要"}。'
            "禁止补充证据中没有的游戏名、角色、时间、奖励或结论；"
            "不要把《鸣潮》写成其它游戏；区分官方信息与用户反馈；存疑时明确说明。"
        )
        user = (
            f"事件标题：{event.title}\n类目：{event.category}\n"
            f"确定的证据数量：{len(evidences)}\n证据列表：\n" + "\n".join(evidences)
        )
        started = time.monotonic()
        try:
            raw_out, usage = await chat(config, system, user, json_mode=True)
            parsed = json.loads(raw_out)
        except Exception as exc:  # noqa: BLE001
            await record_usage(session, task="summarize_event", config=config, usage=None,
                               duration_ms=int((time.monotonic() - started) * 1000),
                               ref_id=event_id, status="failed")
            logger.warning("summarize_event %s failed: %r", event_id, exc)
            return {"status": "failed", "reason": str(exc)[:200]}

        await record_usage(session, task="summarize_event", config=config, usage=usage,
                           duration_ms=int((time.monotonic() - started) * 1000), ref_id=event_id)

        summary = str(parsed.get("summary") or "").strip()[:1000]
        used_fallback = not summary or not _summary_supported(
            summary, "\n".join(evidence_context)
        )
        if used_fallback:
            summary = _fallback_summary(event, len(evidences))

    changed = summary != (event.summary or "")
    event.summary = summary
    event.summary_status = "fallback" if used_fallback else "ai"
    event.summary_updated_at = utcnow()
    from app.models import EventRevision

    if changed:
        session.add(
            EventRevision(
                event_id=event.id,
                actor="engine",
                note=(
                    f"安全回退摘要更新（{config['model'] or '未配置 AI'}）"
                    if used_fallback
                    else f"AI 摘要更新（{config['model']}）"
                ),
            )
        )
    await session.commit()
    return {
        "status": "ok",
        "summary": summary,
        "summary_status": event.summary_status,
        "facts": len(event.facts or []),
    }


async def translate_content(session: AsyncSession, content_id: int) -> dict:
    """外语内容机翻 → zh-CN（存 content_translations，可重算），并记录用量。"""
    config = await get_ai_config(session)
    if not ai_available(config):
        return {"status": "skipped", "reason": "ai_not_configured"}

    row = await session.execute(
        select(RawContent, ContentVersion)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(RawContent.id == content_id)
        .order_by(ContentVersion.version.desc())
        .limit(1)
    )
    pair = row.first()
    if pair is None:
        return {"status": "skipped", "reason": "content_not_found"}
    raw, version = pair
    if raw.platform not in ("kuro_global", "youtube"):
        return {"status": "skipped", "reason": "not_foreign_platform"}
    text = (version.text if version else None) or ""
    title = (version.title if version else None) or raw.external_id
    if not text.strip() and not title.strip():
        return {"status": "skipped", "reason": "nothing_to_translate"}

    system = "你是游戏公告翻译。把用户内容翻译成简体中文，保留专有名词，只输出译文。"
    user = f"标题：{title}\n\n正文：{text[:6000]}"
    started = time.monotonic()
    try:
        translated, usage = await chat(config, system, user)
    except Exception as exc:  # noqa: BLE001
        await record_usage(session, task="translate_content", config=config, usage=None,
                           duration_ms=int((time.monotonic() - started) * 1000),
                           ref_id=content_id, status="failed")
        return {"status": "failed", "reason": str(exc)[:200]}
    await record_usage(session, task="translate_content", config=config, usage=usage,
                       duration_ms=int((time.monotonic() - started) * 1000), ref_id=content_id)

    existing = await session.get(ContentTranslation, content_id)
    if existing is None:
        existing = ContentTranslation(raw_content_id=content_id)
        session.add(existing)
    existing.text = translated[:20000]
    existing.model = config["model"]
    await session.commit()
    return {"status": "ok", "chars": len(translated)}


async def assess_product_stage(session: AsyncSession, product_id: int) -> dict:
    """让模型审核商品履约证据；服务端再校验证据归属和独立账号数量。"""
    product = await session.get(Product, product_id)
    if product is None:
        return {"status": "skipped", "reason": "product_not_found"}

    config = await get_ai_config(session)
    if not ai_available(config):
        return {"status": "skipped", "reason": "ai_not_configured"}

    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    rows = list(
        await session.execute(
            select(RawContent, ContentVersion, Event, Source)
            .join(Evidence, Evidence.raw_content_id == RawContent.id)
            .join(Event, Evidence.event_id == Event.id)
            .join(latest_sq, latest_sq.c.rid == RawContent.id)
            .join(
                ContentVersion,
                (ContentVersion.raw_content_id == latest_sq.c.rid)
                & (ContentVersion.version == latest_sq.c.version),
            )
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(
                Event.product_id == product_id,
                Event.is_active.is_(True),
                Event.category.in_(["unboxing", "arrived"]),
                Evidence.role != "excluded",
            )
            .order_by(RawContent.published_at.desc(), RawContent.id.desc())
            .limit(30)
        )
    )

    candidates: list[dict] = []
    candidate_accounts: dict[int, str] = {}
    seen_ids: set[int] = set()
    for raw, version, event, source in rows:
        if raw.id in seen_ids:
            continue
        seen_ids.add(raw.id)
        account_key = source.external_id if source else f"{raw.platform}:{raw.external_id}"
        candidate_accounts[raw.id] = account_key
        candidates.append(
            {
                "id": raw.id,
                "platform": raw.platform,
                "account": source.name if source else None,
                "account_id": account_key,
                "identity_hint": source.identity if source else "unknown",
                "category_hint": event.category,
                "published_at": raw.published_at.isoformat() if raw.published_at else None,
                "title": (version.title or raw.external_id)[:240],
                "text": _plain_evidence(version.text or "")[:1800],
            }
        )
    if not candidates:
        return {"status": "skipped", "reason": "no_evidences"}

    system = (
        "你是商品履约证据审核员，只能使用输入证据判断，并只输出 JSON："
        '{"stage":0,"reason":"中文理由","evidence_ids":[1]}。'
        "阶段仅允许 0 到 3：0=证据不足；1=明确为送测/媒体样品；"
        "2=至少一名独立普通购买者明确表示已收到、到货或开箱；"
        "3=至少三名独立普通购买者满足阶段2。"
        "商家展示、官方宣传、媒体送测、自费预览、仅提及商品或等待发货都不算普通用户到货。"
        "标题和正文都是有效证据；标题中第一人称的到货、开箱、试用表述可证明该账号展示了实物。"
        "identity_hint=unknown 仅表示系统尚未人工标注，不是否定普通用户身份；"
        "账号未标明官方、商家、媒体或送测，且以个人口吻分享开箱/使用时，可判为普通用户。"
        "必须核对内容确实对应目标商品；不要根据播放量或纯营销措辞推测身份。"
        "evidence_ids 只能选输入中的 id；阶段大于0必须给证据，理由要说明账号性质和到货事实。"
        "理由使用面向普通用户的自然中文，不要出现 identity_hint、category_hint 等内部字段名。"
    )
    user = (
        f"目标商品：{product.name}\n匹配词：{json.dumps(product.watch_terms or [], ensure_ascii=False)}\n"
        f"候选证据：\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    started = time.monotonic()
    try:
        raw_out, usage = await chat(config, system, user, json_mode=True)
        parsed = json.loads(raw_out)
        stage = int(parsed.get("stage"))
        reason = str(parsed.get("reason") or "").strip()[:1000]
        evidence_ids = list(dict.fromkeys(int(item) for item in parsed.get("evidence_ids", [])))
        available_ids = set(candidate_accounts)
        selected = set(evidence_ids)
        if stage not in range(4) or not reason:
            raise ValueError("invalid stage or empty reason")
        if not selected.issubset(available_ids):
            raise ValueError("model selected unavailable evidence")
        if stage > 0 and not selected:
            raise ValueError("positive stage has no evidence")
        if stage >= 2:
            account_count = len({candidate_accounts[item] for item in selected})
            required = 3 if stage == 3 else 1
            if account_count < required:
                raise ValueError(f"stage {stage} has only {account_count} accounts")
    except Exception as exc:  # noqa: BLE001
        await record_usage(
            session,
            task="assess_product",
            config=config,
            usage=locals().get("usage"),
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=product_id,
            status="failed",
        )
        logger.warning("assess_product_stage %s failed: %r", product_id, exc)
        return {"status": "failed", "reason": "model_output_invalid"}

    await record_usage(
        session,
        task="assess_product",
        config=config,
        usage=usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        ref_id=product_id,
    )
    product.stage_hint = stage
    product.stage_hint_source = "ai"
    product.stage_hint_reason = reason
    product.stage_hint_evidence_ids = evidence_ids
    product.stage_hint_updated_at = utcnow()
    effective = max(product.computed_stage, stage)
    label = FULFILLMENT_STAGES[effective]
    events = list(
        (
            await session.execute(
                select(Event).where(
                    Event.product_id == product_id,
                    Event.is_active.is_(True),
                    Event.category.in_(["unboxing", "arrived"]),
                )
            )
        ).scalars()
    )
    for event in events:
        event.status_label = label
        event.last_updated_at = utcnow()
        session.add(
            EventRevision(
                event_id=event.id,
                actor="ai",
                note=f"AI 履约判断：{label}；绑定 {len(evidence_ids)} 条证据",
            )
        )
    await session.commit()
    return {
        "status": "ok",
        "stage": effective,
        "stage_label": label,
        "evidence_ids": evidence_ids,
    }
