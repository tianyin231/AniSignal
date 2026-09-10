"""关键词通知：主动启用、精确匹配、异步入口与每日去重。"""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.models import ContentVersion, NotificationDelivery, RawContent, Source, User, UserKeyword
from app.services import notifications
from app.services.auth import hash_password
from app.scheduler.loop import _morning_due


def test_morning_schedule_uses_beijing_time():
    assert _morning_due(datetime(2026, 8, 27, 23, 59))[0] is False
    assert _morning_due(datetime(2026, 8, 28, 0, 0)) == (True, "2026-08-28")


def test_notification_html_escapes_external_content():
    _, body, html = notifications.render_email(
        "ai",
        date(2026, 8, 28),
        ["爱弥斯<script>"],
        [
            {
                "title": "<script>alert(1)</script>新角色预告",
                "summary": "官方发布了新角色预告。",
                "identity": "official",
                "keywords": ["爱弥斯<script>"],
                "source": "鸣潮官方",
                "platform": "bilibili",
                "published_at": datetime(2026, 8, 28, 1, 0),
                "url": "javascript:alert(1)",
                "image": "javascript:alert(2)",
                "importance": True,
            }
        ],
    )

    assert "今日结论" in body
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "javascript:" not in html


def test_notification_filters_low_signal_community_content():
    base = {"identity": "unknown", "category": None}
    assert notifications._is_low_signal_notification(
        {**base, "title": "看鸣潮动画短片后彻底破防"}
    )
    assert notifications._is_low_signal_notification(
        {**base, "title": "爱弥斯矩阵配队伤害实测"}
    )
    assert notifications._is_low_signal_notification(
        {**base, "title": "爱弥斯联名键盘开箱", "category": "unboxing"}
    )
    assert not notifications._is_low_signal_notification(
        {**base, "title": "爱弥斯联名机械键盘出现售后反馈", "category": "quality"}
    )
    assert not notifications._is_low_signal_notification(
        {**base, "title": "鸣潮官方角色预告", "identity": "official"}
    )


def test_notification_summary_cannot_upgrade_community_to_official():
    community = {"identity": "unknown"}
    assert not notifications._summary_respects_identity(
        community, "鸣潮官方发布了爱弥斯新内容。"
    )
    assert notifications._summary_respects_identity(
        community, "社区用户反馈爱弥斯联名键盘存在按键问题。"
    )


async def _user_with_keyword(session, email="notice@t.com", enabled=True, keyword="爱弥斯"):
    user = User(
        email=email,
        password_hash=hash_password("password123"),
        is_verified=True,
        notification_enabled=enabled,
    )
    session.add(user)
    await session.flush()
    if keyword:
        session.add(UserKeyword(user_id=user.id, keyword=keyword, enabled=True))
    await session.commit()
    return user


async def _content(
    session,
    title: str,
    text: str = "",
    identity: str = "official",
    image: str | None = None,
    community: str = "wuwa",
):
    day = notifications.beijing_today()
    start_at, _ = notifications.morning_window(day)
    source = Source(
        platform="bilibili",
        external_id=f"source-{abs(hash(title))}",
        name="鸣潮官方",
        identity=identity,
        raw_json={},
    )
    session.add(source)
    await session.flush()
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id=f"BV{abs(hash(title))}",
        source_id=source.id,
        url="https://example.com/source",
        published_at=start_at + timedelta(hours=2),
        raw_json={},
        content_hash=title,
        matched_keywords=[],
        community=community,
    )
    session.add(raw)
    await session.flush()
    session.add(
        ContentVersion(
            raw_content_id=raw.id,
            version=1,
            title=title,
            text=text,
            normalized_json={"media": [image] if image else []},
            category="official_announce",
        )
    )
    await session.commit()
    return raw


async def test_manual_morning_covers_all_communities_without_keyword_filter(session, monkeypatch):
    user = await _user_with_keyword(session)
    matching = await _content(
        session,
        "爱弥斯联名键盘发货说明",
        image="http://i0.hdslb.com/example-cover.jpg",
    )
    other = await _content(session, "碧蓝航线活动公告", community="blhx")
    hsr = await _content(session, "星穹铁道角色预告", community="hsr")
    arknights = await _content(session, "明日方舟版本公告", community="arknights")
    hidden = await _content(session, "已下架公告")
    hidden.hidden = True
    old = await _content(session, "过期公告")
    old.published_at -= timedelta(days=2)
    await session.commit()
    sent: dict = {}

    async def fake_send(to, subject, body, smtp=None, *, html_body=None):
        sent.update(to=to, subject=subject, body=body, html_body=html_body)
        return True

    monkeypatch.setattr(notifications, "send_email", fake_send)
    delivery = await notifications.create_delivery(
        session, user.id, "morning", "manual", day=notifications.beijing_today()
    )
    result = await notifications.process_delivery(session, delivery.id)

    assert result == {"status": "sent", "items": 4}
    assert "爱弥斯联名键盘发货说明" in sent["body"]
    assert "碧蓝航线活动公告" in sent["body"]
    assert "星穹铁道角色预告" in sent["body"]
    assert "明日方舟版本公告" in sent["body"]
    assert "已下架公告" not in sent["body"]
    assert "过期公告" not in sent["body"]
    assert "全站早报" in sent["subject"]
    assert "内容范围：全站所有游戏分区" in sent["body"]
    assert "关注关键词" not in sent["body"]
    assert "今日结论" in sent["body"]
    assert "https://i0.hdslb.com/example-cover.jpg@672w_1e_1c.jpg" in sent["html_body"]
    assert "查看原文" in sent["html_body"]
    assert "width:100%" in sent["html_body"]
    await session.refresh(delivery)
    assert set(delivery.content_ids) == {matching.id, other.id, hsr.id, arknights.id}
    assert delivery.matched_keywords == []


async def test_personal_notification_candidates_still_require_keywords(session):
    matching = await _content(session, "爱弥斯联名键盘公告")
    await _content(session, "碧蓝航线活动公告", community="blhx")
    start_at, end_at = notifications.morning_window(notifications.beijing_today())

    candidates = await notifications.load_candidates(session, ["爱弥斯"], start_at, end_at)
    assert [item["id"] for item in candidates] == [matching.id]
    assert candidates[0]["keywords"] == ["爱弥斯"]
    assert await notifications.load_candidates(session, [], start_at, end_at) == []


async def test_scheduled_morning_sends_official_content_without_keywords_or_ai(session, monkeypatch):
    user = await _user_with_keyword(session, keyword=None)
    await _content(session, "联名键盘动态", text="正文顺带提到爱弥斯")

    async def no_decision(*args, **kwargs):
        return None, "AI unavailable"

    async def fake_send(*args, **kwargs):
        return True

    monkeypatch.setattr(notifications, "ai_curate", no_decision)
    monkeypatch.setattr(notifications, "send_email", fake_send)
    delivery = await notifications.create_delivery(
        session,
        user.id,
        "morning",
        "schedule",
        day=notifications.beijing_today(),
        dedupe_key=f"test:{user.id}",
    )
    result = await notifications.process_delivery(session, delivery.id)
    assert result == {"status": "sent", "items": 1}


async def test_scheduled_morning_sends_empty_digest_when_no_qualified_updates(session, monkeypatch):
    user = await _user_with_keyword(session, email="community@t.com")
    await _content(session, "爱弥斯闲聊视频", identity="user")

    async def no_decision(*args, **kwargs):
        return [], None

    async def fake_send(to, subject, body, **kwargs):
        assert "暂未发现全站可收录的新信息" in body
        assert "爱弥斯闲聊视频" not in body
        return True

    monkeypatch.setattr(notifications, "ai_curate", no_decision)
    monkeypatch.setattr(notifications, "send_email", fake_send)
    delivery = await notifications.create_delivery(
        session,
        user.id,
        "morning",
        "schedule",
        day=notifications.beijing_today(),
        dedupe_key=f"community:{user.id}",
    )
    result = await notifications.process_delivery(session, delivery.id)
    assert result == {"status": "sent", "items": 0}


async def test_notification_preference_and_manual_queue(client, session, fake_queue):
    from tests.test_account_api import _register_and_login

    await _register_and_login(client, session, email="queue@t.com")

    disabled = await client.post("/api/account/notifications/send", json={"kind": "morning"})
    assert disabled.status_code == 400

    enabled = await client.put("/api/account/notifications", json={"enabled": True})
    assert enabled.json()["enabled"] is True
    morning = await client.post("/api/account/notifications/send", json={"kind": "morning"})
    assert morning.status_code == 202
    missing_keywords = await client.post("/api/account/notifications/send", json={"kind": "ai"})
    assert missing_keywords.status_code == 400
    await client.post("/api/account/keywords", json={"keyword": "爱弥斯"})
    queued = await client.post("/api/account/notifications/send", json={"kind": "ai"})
    assert queued.status_code == 202
    assert "几分钟" in queued.json()["message"]
    assert fake_queue.notifications == [morning.json()["delivery_id"], queued.json()["delivery_id"]]

    status = await client.get("/api/account/notifications")
    assert status.json()["records"][0]["status"] == "pending"


async def test_scheduled_batch_is_deduplicated(session, monkeypatch):
    user = await _user_with_keyword(session, email="daily@t.com", keyword=None)
    await _user_with_keyword(session, email="disabled@t.com", enabled=False, keyword=None)
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
    assert first["users"] == 1
    assert second["sent"] == 0
    assert sent == 1
    rows = list(await session.scalars(select(NotificationDelivery).where(NotificationDelivery.user_id == user.id)))
    assert len(rows) == 1


async def test_manual_morning_resends_same_day_scheduled_snapshot(session, monkeypatch):
    user = await _user_with_keyword(session, email="snapshot@t.com")
    content = await _content(session, "爱弥斯官方角色预告")
    sent: list[dict] = []

    async def fake_send(to, subject, body, smtp=None, *, html_body=None):
        sent.append({"subject": subject, "body": body, "html_body": html_body})
        return True

    async def fake_onepush(*args, **kwargs):
        return {"ok": True, "status": "sent"}

    monkeypatch.setattr(notifications, "send_email", fake_send)
    monkeypatch.setattr("app.services.notifier.send_onepush", fake_onepush)
    scheduled = await notifications.run_scheduled_morning(session)
    assert scheduled["sent"] == 1

    async def should_not_rebuild(*args, **kwargs):
        raise AssertionError("同日手动早报不应重新读取候选")

    monkeypatch.setattr(notifications, "load_candidates", should_not_rebuild)
    delivery = await notifications.create_delivery(
        session,
        user.id,
        "morning",
        "manual",
        day=notifications.beijing_today(),
    )
    result = await notifications.process_delivery(session, delivery.id)

    assert result == {"status": "sent", "items": 1}
    assert sent[1] == sent[0]
    await session.refresh(delivery)
    assert delivery.content_ids == [content.id]
    assert delivery.html_body == sent[0]["html_body"]
