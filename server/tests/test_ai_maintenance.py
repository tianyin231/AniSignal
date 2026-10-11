from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models import Event, ContentTranslation
from app.services import ai_maintenance
from app.services.queue import ArqQueue


async def test_ai_batch_uses_stable_per_item_jobs():
    redis = SimpleNamespace(enqueue_job=AsyncMock(side_effect=[None, SimpleNamespace(job_id="job")]))
    queue = ArqQueue(redis)
    assert await queue.enqueue_ai_batch("summarize_event", [1, 1, 2], only_missing=False) == 1
    assert [call.args for call in redis.enqueue_job.await_args_list] == [
        ("repair_ai_item", "summarize_event", 1, False), ("repair_ai_item", "summarize_event", 2, False),
    ]
    assert redis.enqueue_job.await_args_list[0].kwargs["_job_id"] == "ai-repair:summarize_event:1"


async def test_ai_repair_rechecks_manual_and_completed_results(session, monkeypatch):
    summarize = AsyncMock(return_value={"status": "ok"})
    translate = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(ai_maintenance, "summarize_event", summarize)
    monkeypatch.setattr(ai_maintenance, "translate_content", translate)
    event = Event(title="版本更新", category="version_update", summary="人工修正", summary_status="manual")
    session.add(event)
    await session.commit()
    assert (await ai_maintenance.repair_ai_item(session, "summarize_event", event.id, False))["status"] == "skipped"
    event.summary_status = "ai"
    assert (await ai_maintenance.repair_ai_item(session, "summarize_event", event.id))["status"] == "skipped"
    event.summary_status = "fallback"
    assert (await ai_maintenance.repair_ai_item(session, "summarize_event", event.id))["status"] == "ok"
    summarize.assert_awaited_once()
    # 外键在该 SQLite fixture 未开启，翻译存在性本身无需伪造原始内容。
    session.add(ContentTranslation(raw_content_id=99, text="已完成", model="test"))
    await session.commit()
    assert (await ai_maintenance.repair_ai_item(session, "translate_content", 99))["status"] == "skipped"
    translate.assert_not_awaited()
