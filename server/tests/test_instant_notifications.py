from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import Event, EventRevision, Evidence, NotificationDelivery, User
from app.services import instant_notifications as instant
from tests.test_notifications import _content, _user_with_keyword


async def _event(session, *, category="version_update", importance=False):
    raw = await _content(session, f"爱弥斯新版本开放 {category}")
    event = Event(title="爱弥斯新版本开放", category=category, identity="official", importance=importance)
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id))
    revision = EventRevision(event_id=event.id, actor="engine", note="事件由聚合引擎创建")
    session.add(revision)
    await session.commit()
    return event, revision, raw


async def test_preference_opt_in_does_not_replay_history(client, session):
    from tests.test_account_api import _register_and_login

    await _register_and_login(client, session, email="instant@t.com")
    _, old_revision, _ = await _event(session)
    assert (await client.put("/api/account/notifications", json={"instant_enabled": True})).status_code == 400
    await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})
    response = await client.put("/api/account/notifications", json={"enabled": True, "instant_enabled": True})
    assert response.status_code == 200
    assert response.json()["instant_enabled"] is True
    user = await session.scalar(select(User).where(User.email == "instant@t.com"))
    assert user.instant_notification_cursor == old_revision.id
    assert await instant.prepare_instant_notifications(session) == 0
    disabled = await client.put("/api/account/notifications", json={"enabled": False})
    assert disabled.json()["instant_enabled"] is True
    assert disabled.json()["enabled"] is False
    # 总开关恢复同样不回扫停用期间的修订。
    event, revision, _ = await _event(session, category="shipping")
    await client.put("/api/account/notifications", json={"enabled": True})
    await session.refresh(user)
    assert user.instant_notification_cursor == revision.id
    assert await instant.prepare_instant_notifications(session) == 0


async def test_important_changes_coalesce_and_send_once(session, monkeypatch):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    event, _, _ = await _event(session)
    session.add(EventRevision(event_id=event.id, note="新增证据 1 条（bilibili）"))
    await session.commit()
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(instant, "send_email", sender)
    result = await instant.run_instant_notifications(session)
    assert result == {"created": 1, "sent": 1, "failed": 0, "skipped": 0}
    assert (await instant.run_instant_notifications(session))["sent"] == 0
    sender.assert_awaited_once()
    row = await session.scalar(select(NotificationDelivery))
    assert row.kind == "instant" and row.status == "sent"
    assert "新增证据" in row.body
    assert row.matched_keywords == ["爱弥斯"] and len(row.content_ids) == 1
    session.add(EventRevision(event_id=event.id, note="AI 摘要更新（test）"))
    await session.commit()
    assert await instant.prepare_instant_notifications(session) == 0


async def test_only_important_matching_events_are_queued(session):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    await _event(session, category="official_announce")
    assert await instant.prepare_instant_notifications(session) == 0
    event, _, _ = await _event(session, category="quality", importance=True)
    assert await instant.prepare_instant_notifications(session) == 1
    # 修改排除词后，新的修订不应触发。
    user.feed_preferences = {"exclude_keywords": ["新版本"]}
    session.add(EventRevision(event_id=event.id, note="人工更正重要变化"))
    await session.commit()
    assert await instant.prepare_instant_notifications(session) == 0


@pytest.mark.parametrize("change", ["disabled", "instant_disabled", "keyword_disabled", "hidden", "inactive", "excluded", "banned"])
async def test_send_rechecks_subscription_and_evidence(session, monkeypatch, change):
    from app.models import UserKeyword

    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    event, _, raw = await _event(session)
    assert await instant.prepare_instant_notifications(session) == 1
    row = await session.scalar(select(NotificationDelivery))
    if change == "disabled":
        user.notification_enabled = False
    elif change == "instant_disabled":
        user.instant_notification_enabled = False
    elif change == "keyword_disabled":
        keyword = await session.scalar(select(UserKeyword).where(UserKeyword.user_id == user.id))
        keyword.enabled = False
    elif change == "hidden":
        raw.hidden = True
    elif change == "inactive":
        event.is_active = False
    elif change == "excluded":
        user.feed_preferences = {"communities": ["hsr"]}
    else:
        user.status = "banned"
    await session.commit()
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(instant, "send_email", sender)
    assert await instant.send_instant_notification(session, row.id) == {"status": "skipped"}
    sender.assert_not_awaited()


async def test_failed_send_is_recorded_without_automatic_duplicate(session, monkeypatch):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    await _event(session)
    sender = AsyncMock(return_value=False)
    monkeypatch.setattr(instant, "send_email", sender)
    assert (await instant.run_instant_notifications(session))["failed"] == 1
    assert (await instant.run_instant_notifications(session))["failed"] == 0
    sender.assert_awaited_once()


@pytest.mark.parametrize("note", ["人工锁定，引擎暂停改动", "解除锁定，恢复引擎维护", "人工编辑字段：summary", "人工编辑字段：summary, manual_locked", "安全回退摘要更新（未配置 AI）"])
async def test_maintenance_revisions_do_not_send_mail(session, note):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    event, revision, _ = await _event(session)
    user.instant_notification_cursor = revision.id
    session.add(EventRevision(event_id=event.id, note=note))
    await session.commit()
    assert await instant.prepare_instant_notifications(session) == 0


async def test_sender_exception_does_not_block_next_delivery(session, monkeypatch):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    await _event(session)
    await _event(session, category="shipping")
    sender = AsyncMock(side_effect=[ValueError("invalid mail header"), True])
    monkeypatch.setattr(instant, "send_email", sender)
    result = await instant.run_instant_notifications(session)
    assert result == {"created": 2, "sent": 1, "failed": 1, "skipped": 0}
    rows = list(await session.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id)))
    assert [row.status for row in rows] == ["failed", "sent"]


async def test_new_business_revision_supersedes_pending_event_notification(session, monkeypatch):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    event, _, _ = await _event(session)
    assert await instant.prepare_instant_notifications(session) == 1
    session.add(EventRevision(event_id=event.id, note="新增证据 2 条（bilibili）"))
    await session.commit()
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(instant, "send_email", sender)

    result = await instant.run_instant_notifications(session)

    assert result == {"created": 1, "sent": 1, "failed": 0, "skipped": 1}
    rows = list(await session.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id)))
    assert [row.status for row in rows] == ["skipped", "sent"]
    sender.assert_awaited_once()
    assert "新增证据 2 条" in sender.await_args.args[2]


@pytest.mark.parametrize("note", [
    "AI 摘要更新（test）", "安全回退摘要更新（test）", "人工锁定，引擎暂停改动",
    "解除锁定，恢复引擎维护", "人工编辑字段：summary, manual_locked",
])
async def test_maintenance_revision_keeps_pending_event_notification(session, monkeypatch, note):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    event, _, _ = await _event(session)
    assert await instant.prepare_instant_notifications(session) == 1
    session.add(EventRevision(event_id=event.id, note=note))
    await session.commit()
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(instant, "send_email", sender)

    result = await instant.run_instant_notifications(session)

    assert result == {"created": 0, "sent": 1, "failed": 0, "skipped": 0}
    sender.assert_awaited_once()
