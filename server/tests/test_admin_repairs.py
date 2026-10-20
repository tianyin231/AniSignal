"""后台补处理只入队，筛选不遗漏旧缺口，也不覆盖人工结果。"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.clock import utcnow
from app.models import ContentTranslation, ContentVersion, Event, Evidence, RawContent


@pytest.mark.parametrize("platform,target_type,expected", [
    ("bilibili", "keyword_search", 10000),
    ("bilibili", "account_videos", 10000),
    ("bilibili", "account_dynamics", 0),
    ("youtube", "channel_rss", 0),
    ("miyoushe", "keyword_search", 0),
])
async def test_target_view_threshold_matches_collector_capability(client, admin_headers, platform, target_type, expected):
    # 目标值要符合该 (platform, target_type) 的形态：账号类必须是数字 MID / 频道 ID
    value = {
        "account_videos": "12345",
        "account_dynamics": "12345",
        "channel_rss": "UCabcdefghijklmnopqrstuv",
        "notices": "default",
    }.get(target_type, "test")
    payload = {"platform": platform, "target_type": target_type, "target_value": value}
    response = await client.post("/api/admin/collect-targets", json=payload, headers=admin_headers)
    assert response.status_code == 201
    assert response.json()["min_views"] == expected
    response = await client.put(f"/api/admin/collect-targets/{response.json()['id']}",
        json={"min_views": 20000}, headers=admin_headers)
    assert response.json()["min_views"] == (20000 if expected else 0)
    response = await client.post("/api/admin/collect-targets",
        json={**payload, "target_value": value + "1", "min_views": 30000}, headers=admin_headers)
    assert response.json()["min_views"] == (30000 if expected else 0)


async def test_news_target_defaults_to_no_view_threshold(client, admin_headers):
    response = await client.post("/api/admin/collect-targets", headers=admin_headers,
        json={"platform": "bilibili", "target_type": "keyword_search", "target_value": "Codex", "community": "ai"})
    assert response.status_code == 201 and response.json()["min_views"] == 0
    response = await client.put(f"/api/admin/collect-targets/{response.json()['id']}",
        json={"min_views": 20000}, headers=admin_headers)
    assert response.json()["min_views"] == 0


@pytest.mark.parametrize("path", ["/api/admin/ai/summarize-all", "/api/admin/settings/ai/summarize-all"])
@pytest.mark.parametrize("only_missing", [True, False])
async def test_summary_batch_includes_fallback_but_preserves_manual(client, session, admin_headers, fake_queue, path, only_missing):
    rows = [
        Event(title="缺失", category="announcement"),
        Event(title="降级", category="announcement", summary="模板", summary_status="fallback"),
        Event(title="AI", category="announcement", summary="AI", summary_status="ai"),
        Event(title="锁定", category="announcement", manual_locked=True),
        Event(title="人工", category="announcement", summary="人工", summary_status="manual"),
        Event(title="停用", category="announcement", is_active=False),
    ]
    session.add_all(rows)
    await session.commit()
    fake_queue.enqueue_ai_batch = AsyncMock(return_value=2 if only_missing else 3)
    response = await client.post(path, params={"only_missing": str(only_missing).lower()}, headers=admin_headers)
    expected = [row.id for row in rows[:2 if only_missing else 3]]
    assert response.status_code == 200
    assert response.json() == {"candidates": len(expected), "queued": len(expected), "remaining": len(expected)}
    fake_queue.enqueue_ai_batch.assert_awaited_once_with("summarize_event", expected, only_missing=only_missing)


@pytest.mark.parametrize("missing_count", [1, 51])
async def test_translation_batch_finds_missing_older_than_latest_100(client, session, admin_headers, fake_queue, missing_count):
    now = utcnow()
    missing_ids = []
    for index in range(missing_count + 100):
        raw = RawContent(platform="youtube", content_type="video", external_id=str(index),
                         collected_at=now + timedelta(seconds=index))
        session.add(raw)
        await session.flush()
        if index < missing_count:
            missing_ids.append(raw.id)
        else:
            session.add(ContentTranslation(raw_content_id=raw.id, text="已翻译"))
    await session.commit()
    fake_queue.enqueue_ai_batch = AsyncMock(return_value=min(50, missing_count))
    response = await client.post("/api/admin/ai/translate-missing", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"candidates": min(50, missing_count), "queued": min(50, missing_count), "remaining": missing_count}
    fake_queue.enqueue_ai_batch.assert_awaited_once_with("translate_content", list(reversed(missing_ids))[:50])


async def test_summary_batch_reports_already_queued_and_queue_unavailable(client, session, admin_headers, fake_queue):
    session.add(Event(title="待补", category="announcement"))
    await session.commit()
    fake_queue.enqueue_ai_batch = AsyncMock(return_value=0)
    response = await client.post("/api/admin/ai/summarize-all", headers=admin_headers)
    assert response.status_code == 200 and response.json() == {"candidates": 1, "queued": 0, "remaining": 1}
    fake_queue.enqueue_ai_batch.side_effect = RuntimeError("offline")
    response = await client.post("/api/admin/ai/summarize-all", headers=admin_headers)
    assert response.status_code == 503


async def test_media_visibility_can_remove_last_platform_override(client, admin_headers):
    path = "/api/admin/settings/media-visibility"
    await client.put(path, json={"platforms": {"youtube": {"video": "hide"}}}, headers=admin_headers)
    response = await client.put(path, json={"platforms": {}}, headers=admin_headers)
    assert response.status_code == 200 and response.json()["platforms"] == {}
    response = await client.get("/api/site-config")
    assert response.json()["media_visibility"]["platforms"] == {}


async def test_summary_preserves_manual_edit_while_model_is_running(session, monkeypatch):
    from app.services import ai
    from app.services.settings import set_setting

    await set_setting(session, "ai", {"base_url": "https://example.com", "model": "test", "api_key": "test"})
    event = Event(title="鸣潮新版本公告", category="announcement", summary="旧模板", summary_status="fallback")
    raw = RawContent(platform="kuro_cn", content_type="notice", external_id="manual-during-summary")
    session.add_all([event, raw])
    await session.flush()
    session.add_all([
        ContentVersion(raw_content_id=raw.id, title=event.title, text="鸣潮新版本公告已发布。"),
        Evidence(event_id=event.id, raw_content_id=raw.id),
    ])
    await session.commit()

    async def model_response(*args, **kwargs):
        await session.execute(update(Event).where(Event.id == event.id).values(
            summary="人工确认的摘要", summary_status="manual", manual_locked=True,
        ).execution_options(synchronize_session=False))
        await session.commit()
        return '{"summary":"鸣潮新版本公告已发布。"}', None

    monkeypatch.setattr(ai, "chat", model_response)
    result = await ai.summarize_event(session, event.id)
    await session.refresh(event)
    assert result == {"status": "skipped", "reason": "manual_locked"}
    assert event.summary == "人工确认的摘要" and event.summary_status == "manual"
