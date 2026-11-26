"""管理员补处理任务：执行时重查状态，保护已修正内容。"""

from sqlalchemy import select

from app.models import ContentBrief, ContentTranslation, Event
from app.services.ai import summarize_event, translate_content
from app.services.content_brief import brief_content

AI_TASKS = {"summarize_event", "translate_content", "brief_content"}


async def repair_ai_item(session, task: str, ref_id: int, only_missing: bool = True) -> dict:
    if task == "summarize_event":
        event = await session.get(Event, ref_id)
        if not event or not event.is_active or event.manual_locked or event.summary_status == "manual":
            return {"status": "skipped", "reason": "event_not_eligible"}
        if only_missing and event.summary is not None and event.summary_status != "fallback":
            return {"status": "skipped", "reason": "already_summarized"}
        return await summarize_event(session, ref_id)
    if task == "translate_content":
        existing = await session.scalar(select(ContentTranslation.raw_content_id).where(ContentTranslation.raw_content_id == ref_id))
        if existing is not None:
            return {"status": "skipped", "reason": "already_translated"}
        return await translate_content(session, ref_id)
    if task == "brief_content":
        if only_missing:
            existing = await session.scalar(select(ContentBrief.raw_content_id).where(ContentBrief.raw_content_id == ref_id))
            if existing is not None:
                return {"status": "skipped", "reason": "already_briefed"}
        return await brief_content(session, ref_id, force=not only_missing)
    raise ValueError("不支持的 AI 补处理任务")
