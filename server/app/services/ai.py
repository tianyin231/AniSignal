"""AI 层：OpenAI 兼容接口的轻封装 + 事件摘要/内容翻译任务 + 用量记账。

配置来源：site_settings('ai') 优先，环境变量（ANISIGNAL_AI_*）兜底；
未配置时所有任务安全跳过（框架就绪，配好 key 即生效）。
定价（每百万 token 单价）在 ai 设置里配置，费用 = tokens × 单价 / 1e6。
"""

import json
import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AiUsage,
    ContentTranslation,
    ContentVersion,
    Event,
    Evidence,
    RawContent,
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


async def chat(config: dict, system: str, user: str, *, json_mode: bool = False) -> tuple[str, dict | None]:
    """OpenAI 兼容 chat/completions；返回 (回复文本, usage)。"""
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


async def summarize_event(session: AsyncSession, event_id: int) -> dict:
    """AI 汇总事件证据 → 结论摘要 + 关键事实（覆盖引擎模板），并记录用量。"""
    config = await get_ai_config(session)
    if not ai_available(config):
        return {"status": "skipped", "reason": "ai_not_configured"}

    event = await session.get(Event, event_id)
    if event is None:
        return {"status": "skipped", "reason": "event_not_found"}

    rows = await session.execute(
        select(RawContent, ContentVersion, Evidence)
        .join(Evidence, Evidence.raw_content_id == RawContent.id)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(Evidence.event_id == event_id)
        .order_by(RawContent.published_at.asc())
        .limit(12)
    )
    evidences = []
    seen: set[int] = set()
    for raw, version, _ in rows:
        if raw.id in seen:
            continue
        seen.add(raw.id)
        evidences.append(
            f"- [{raw.platform}] {(version.title or raw.external_id)[:60]}：{(version.text or '')[:200]}"
        )
    if not evidences:
        return {"status": "skipped", "reason": "no_evidences"}

    system = (
        "你是游戏周边情报编辑。根据给定的多平台证据，输出 JSON："
        '{"summary": "100字以内中文结论摘要", "facts": [{"label": "标签", "value": "值"}]}。'
        "facts 包含：发生了什么、时间、来源数量；不要编造证据中没有的信息；"
        "区分官方信息与用户反馈；存疑时明确说明。"
    )
    user = f"事件标题：{event.title}\n类目：{event.category}\n证据列表：\n" + "\n".join(evidences)
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

    summary = str(parsed.get("summary") or "")[:1000]
    facts = parsed.get("facts") or []
    facts = [
        {"label": str(f.get("label", ""))[:16], "value": str(f.get("value", ""))[:100]}
        for f in facts
        if isinstance(f, dict) and f.get("label") and f.get("value")
    ][:8]
    if not summary:
        return {"status": "failed", "reason": "empty_summary"}

    changed = summary != (event.summary or "") or facts != (event.facts or [])
    event.summary = summary
    if facts:
        event.facts = facts
    from app.clock import utcnow
    from app.models import EventRevision

    if changed and not event.manual_locked:
        session.add(
            EventRevision(
                event_id=event.id,
                actor="engine",
                note=f"AI 摘要更新（{config['model']}）",
            )
        )
    await session.commit()
    return {"status": "ok", "summary": summary, "facts": len(facts)}


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
