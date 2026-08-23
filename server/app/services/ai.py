"""AI 层：OpenAI 兼容接口的轻封装 + 事件摘要/内容翻译任务。

配置来源：site_settings('ai') 优先，环境变量（ANISIGNAL_AI_*）兜底；
未配置时所有任务安全跳过（框架就绪，配好 key 即生效）。
"""

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ContentTranslation, ContentVersion, Event, Evidence, RawContent

logger = logging.getLogger(__name__)


async def get_ai_config(session: AsyncSession) -> dict:
    from app.services.settings import get_setting

    env = get_settings()
    stored = (await get_setting(session, "ai")) or {}
    return {
        "base_url": (stored.get("base_url") or env.ai_base_url or "").rstrip("/"),
        "api_key": stored.get("api_key") or env.ai_api_key or "",
        "model": stored.get("model") or env.ai_model or "",
    }


def ai_available(config: dict) -> bool:
    return bool(config.get("base_url") and config.get("api_key") and config.get("model"))


async def chat(config: dict, system: str, user: str, *, json_mode: bool = False) -> str:
    """OpenAI 兼容 chat/completions 调用，返回首个回复文本。"""
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
    return data["choices"][0]["message"]["content"]


def _public_view(config: dict) -> dict:
    return {
        "base_url": config["base_url"],
        "model": config["model"],
        "api_key_set": bool(config["api_key"]),
    }


# ---------- 任务实现（worker 与管理端共用） ----------


async def summarize_event(session: AsyncSession, event_id: int) -> dict:
    """AI 汇总事件证据 → 结论摘要 + 关键事实（覆盖引擎模板）。"""
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
    try:
        raw_out = await chat(config, system, user, json_mode=True)
        parsed = json.loads(raw_out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize_event %s failed: %r", event_id, exc)
        return {"status": "failed", "reason": str(exc)[:200]}

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
    event.last_updated_at = (
        event.last_updated_at
    )  # 摘要更新不算状态变化，不触发排序抖动
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
    """外语内容机翻 → zh-CN（存 content_translations，可重算）。"""
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
    try:
        translated = await chat(config, system, user)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "reason": str(exc)[:200]}

    existing = await session.get(ContentTranslation, content_id)
    if existing is None:
        existing = ContentTranslation(raw_content_id=content_id)
        session.add(existing)
    existing.text = f"【{translated.splitlines()[0] if translated else ''}】\n\n{translated}"
    existing.model = config["model"]
    await session.commit()
    return {"status": "ok", "chars": len(translated)}
