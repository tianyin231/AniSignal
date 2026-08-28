"""关键词通知：主动启用、精确匹配、异步入口与每日去重。"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import ContentVersion, NotificationDelivery, RawContent, User, UserKeyword
from app.services import notifications
from app.services.auth import hash_password
from app.scheduler.loop import _morning_due


def test_morning_schedule_uses_beijing_time():
    assert _morning_due(datetime(2026, 8, 27, 23, 59))[0] is False
    assert _morning_due(datetime(2026, 8, 28, 0, 0)) == (True, "2026-08-28")


async def _user_with_keyword(session, email="notice@t.com", enabled=True):
    user = User(
        email=email,
        password_hash=hash_password("password123"),
        is_verified=True,
        notification_enabled=enabled,
    )
    session.add(user)
    await session.flush()
    session.add(UserKeyword(user_id=user.id, keyword="爱弥斯", enabled=True))
    await session.commit()
    return user


async def _content(session, title: str, text: str = ""):
    day = notifications.beijing_today()
    start_at, _ = notifications.morning_window(day)
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id=f"BV{abs(hash(title))}",
        url="https://example.com/source",
        published_at=start_at + timedelta(hours=2),
        raw_json={},
        content_hash=title,
        matched_keywords=[],
    )
    session.add(raw)
    await session.flush()
    session.add(
        ContentVersion(
            raw_content_id=raw.id,
            version=1,
            title=title,
            text=text,
            normalized_json={},
            category="official_announce",
        )
    )
    await session.commit()
    return raw


async def test_manual_morning_only_contains_matching_content(session, monkeypatch):
    user = await _user_with_keyword(session)
    matching = await _content(session, "爱弥斯联名键盘发货说明")
    await _content(session, "其它角色活动公告")
    sent: dict = {}

    async def fake_send(to, subject, body, smtp=None):
        sent.update(to=to, subject=subject, body=body)
        return True

    monkeypatch.setattr(notifications, "send_email", fake_send)
    delivery = await notifications.create_delivery(
        session, user.id, "morning", "manual", day=notifications.beijing_today()
    )
    result = await notifications.process_delivery(session, delivery.id)

    assert result == {"status": "sent", "items": 1}
    assert "爱弥斯联名键盘发货说明" in sent["body"]
    assert "其它角色活动公告" not in sent["body"]
    await session.refresh(delivery)
    assert delivery.content_ids == [matching.id]
    assert delivery.matched_keywords == ["爱弥斯"]


async def test_scheduled_morning_skips_weak_match_when_ai_uncertain(session, monkeypatch):
    user = await _user_with_keyword(session)
    await _content(session, "联名键盘动态", text="正文顺带提到爱弥斯")

    async def no_decision(*args, **kwargs):
        return None, "AI unavailable"

    async def should_not_send(*args, **kwargs):
        raise AssertionError("不明确内容不应发送")

    monkeypatch.setattr(notifications, "ai_curate", no_decision)
    monkeypatch.setattr(notifications, "send_email", should_not_send)
    delivery = await notifications.create_delivery(
        session,
        user.id,
        "morning",
        "schedule",
        day=notifications.beijing_today(),
        dedupe_key=f"test:{user.id}",
    )
    result = await notifications.process_delivery(session, delivery.id)
    assert result["reason"] == "no_matches"


async def test_notification_preference_and_manual_queue(client, session, fake_queue):
    from tests.test_account_api import _register_and_login

    await _register_and_login(client, session, email="queue@t.com")
    await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})

    disabled = await client.post("/api/account/notifications/send", json={"kind": "morning"})
    assert disabled.status_code == 400

    enabled = await client.put("/api/account/notifications", json={"enabled": True})
    assert enabled.json()["enabled"] is True
    queued = await client.post("/api/account/notifications/send", json={"kind": "ai"})
    assert queued.status_code == 202
    assert "几分钟" in queued.json()["message"]
    assert fake_queue.notifications == [queued.json()["delivery_id"]]

    status = await client.get("/api/account/notifications")
    assert status.json()["records"][0]["status"] == "pending"


async def test_scheduled_batch_is_deduplicated(session, monkeypatch):
    user = await _user_with_keyword(session, email="daily@t.com")
    await _content(session, "爱弥斯今日消息")
    sent = 0

    async def fake_send(*args, **kwargs):
        nonlocal sent
        sent += 1
        return True

    async def fake_onepush(*args, **kwargs):
        return {"ok": True, "status": "sent"}

    monkeypatch.setattr(notifications, "send_email", fake_send)
    monkeypatch.setattr("app.services.notifier.send_onepush", fake_onepush)
    first = await notifications.run_scheduled_morning(session)
    second = await notifications.run_scheduled_morning(session)

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert sent == 1
    rows = list(await session.scalars(select(NotificationDelivery).where(NotificationDelivery.user_id == user.id)))
    assert len(rows) == 1
