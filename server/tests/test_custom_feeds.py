"""定制监控单测：账号 CRUD、公开目录、采集过滤与推送、管理端处置。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.api import main as api_main
from app.clock import utcnow
from app.models import CustomFeed, CustomFeedItem, NotificationDelivery, User
from app.services.auth import hash_password

admin_headers = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture(autouse=True)
def fake_mail(monkeypatch):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.notifications.send_email", send)
    return send


async def _login(client, session, email: str) -> None:
    user = await session.scalar(select(User).where(User.email == email))
    from app.services.auth import create_session

    token = await create_session(session, user)
    client.cookies.set("anisignal_session", token)
    client.headers["anisignal_session"] = token


@pytest.fixture
def fake_bili(monkeypatch):
    """伪 B 站采集器：关键词返回两条视频（一条 500 播放、一条 50000 播放）。"""
    from tests.test_parsers_bilibili import VIDEO_RAW

    hot = dict(VIDEO_RAW, bvid="BVcustomHot", title="热门定制视频", view_count=50000)
    cold = dict(VIDEO_RAW, bvid="BVcustomCold", title="冷门定制视频", view_count=500)

    class _FakeCollector:
        async def user_dynamics(self, mid, limit):
            return [dict(VIDEO_RAW, bvid="BVdyn1", title="动态内容")]

        async def search_videos(self, keyword, limit):
            return [hot, cold]

    @asynccontextmanager
    async def _slot(_redis, _platform, _seconds):
        yield

    import app.services.custom_feeds as cf

    monkeypatch.setattr(cf, "BilibiliCollector", lambda: _FakeCollector())
    monkeypatch.setattr(cf, "platform_slot", _slot)


async def test_custom_feed_crud_and_visibility(client, session):
    session.add_all(
        [
            User(email="cf-owner@anisignal.dev", display_name="作者", password_hash="x", is_verified=True),
            User(email="cf-other@anisignal.dev", password_hash="x", is_verified=True),
        ]
    )
    await session.commit()

    # 未登录看不到需要登录的接口
    resp = await client.get("/api/account/custom-feeds")
    assert resp.status_code == 401

    owner = await session.scalar(select(User).where(User.email == "cf-owner@anisignal.dev"))
    from app.services.auth import create_session

    token = await create_session(session, owner)
    client.cookies.set("anisignal_session", token)

    resp = await client.post(
        "/api/account/custom-feeds",
        json={"name": "某UP动态", "kind": "uid", "target_value": "123456", "is_public": True},
    )
    assert resp.status_code == 201
    public_feed_id = resp.json()["id"]

    resp = await client.post(
        "/api/account/custom-feeds",
        json={"name": "私密话题", "kind": "keyword", "target_value": "神秘话题", "push_enabled": True},
    )
    assert resp.status_code == 201
    private_feed_id = resp.json()["id"]

    # UID 非数字 400
    resp = await client.post(
        "/api/account/custom-feeds",
        json={"name": "坏", "kind": "uid", "target_value": "abc"},
    )
    assert resp.status_code == 400

    mine = (await client.get("/api/account/custom-feeds")).json()
    assert len(mine) == 2

    # 公开目录：只出现公开监控
    directory = (await client.get("/api/custom/feeds")).json()["items"]
    assert [f["id"] for f in directory] == [public_feed_id]

    # 私有监控：所有者可见
    detail = await client.get(f"/api/custom/feeds/{private_feed_id}")
    assert detail.status_code == 200 and detail.json()["is_owner"] is True

    # 删除公开监控
    resp = await client.delete(f"/api/account/custom-feeds/{public_feed_id}")
    assert resp.status_code == 204
    assert (await client.get("/api/custom/feeds")).json()["total"] == 0

    # 解绑登录后私有监控 404
    client.cookies.delete("anisignal_session")
    assert (await client.get(f"/api/custom/feeds/{private_feed_id}")).status_code == 404


async def test_custom_feed_sweep_filters_and_dedupes(client, session, fake_bili):
    session.add(
        User(
            email="sweep-owner@anisignal.dev",
            password_hash=hash_password("anisignal123"),
            is_verified=True,
            notification_enabled=True,
        )
    )
    await session.commit()
    await _login(client, session, "sweep-owner@anisignal.dev")

    resp = await client.post(
        "/api/account/custom-feeds",
        json={"name": "热门监控", "kind": "keyword", "target_value": "鸣潮", "min_views": 10000, "push_enabled": True},
    )
    feed_id = resp.json()["id"]

    from app.services.custom_feeds import sweep_custom_feeds

    stats = await sweep_custom_feeds(session, redis=None)
    assert stats["feeds"] == 1 and stats["new_items"] == 1  # 500 播放的被门槛过滤

    items = list(
        (await session.execute(select(CustomFeedItem).where(CustomFeedItem.feed_id == feed_id))).scalars()
    )
    assert len(items) == 1 and items[0].external_id == "BVcustomHot"

    # 推送已创建并通过 mock 邮件服务发送，不连接真实 SMTP。
    delivery = await session.scalar(
        select(NotificationDelivery).where(NotificationDelivery.kind == "custom")
    )
    assert delivery is not None and delivery.dedupe_key.startswith(f"custom-push:{feed_id}:")

    # 第二轮：同条目不重复入库
    stats2 = await sweep_custom_feeds(session, redis=None)
    assert stats2["new_items"] == 0


async def test_admin_custom_feed_management(client, session, fake_bili):
    session.add(
        User(email="manage-me@anisignal.dev", password_hash="x", is_verified=True)
    )
    await session.commit()
    await _login(client, session, "manage-me@anisignal.dev")
    feed_id = (
        await client.post(
            "/api/account/custom-feeds",
            json={"name": "待管理监控", "kind": "keyword", "target_value": "碧蓝航线"},
        )
    ).json()["id"]
    client.headers.pop("anisignal_session")

    resp = await client.get("/api/admin/custom-feeds", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["owner_email"] == "manage-me@anisignal.dev"

    # 停用后不进调度
    resp = await client.patch(
        f"/api/admin/custom-feeds/{feed_id}", json={"status": "disabled"}, headers=admin_headers
    )
    assert resp.json()["status"] == "disabled"

    # 所有者仍可查看，但管理员停用的公开监控不能通过详情直链继续公开。
    resp = await client.patch(f"/api/admin/custom-feeds/{feed_id}", json={"is_public": True}, headers=admin_headers)
    assert resp.status_code == 200
    assert (await client.get(f"/api/custom/feeds/{feed_id}")).status_code == 200
    client.cookies.delete("anisignal_session")
    assert (await client.get(f"/api/custom/feeds/{feed_id}")).status_code == 404

    # 删除
    resp = await client.delete(f"/api/admin/custom-feeds/{feed_id}", headers=admin_headers)
    assert resp.status_code == 204
    assert (await client.get("/api/admin/custom-feeds", headers=admin_headers)).json()["total"] == 0


@pytest.mark.parametrize("has_feed", [False, True])
async def test_worker_collect_custom_feeds(session, session_factory, fake_bili, monkeypatch, has_feed):
    import app.db as db
    from app.worker.settings import collect_custom_feeds

    monkeypatch.setattr(db, "SessionLocal", session_factory)
    if has_feed:
        user = User(email="worker-owner@anisignal.dev", password_hash="x", is_verified=True)
        session.add(user)
        await session.flush()
        feed = CustomFeed(
            user_id=user.id,
            name="热门监控",
            kind="keyword",
            target_value="鸣潮",
            min_views=10000,
        )
        session.add(feed)
        await session.commit()

    # 走生产 worker 入口和真实数据库，覆盖空轮询及到期源入库。
    stats = await collect_custom_feeds({"redis": None})

    expected = 1 if has_feed else 0
    assert stats == {"feeds": expected, "new_items": expected, "pushed": 0, "failed": 0}
    items = list((await session.execute(select(CustomFeedItem))).scalars())
    assert len(items) == expected
    if has_feed:
        assert items[0].external_id == "BVcustomHot"
        await session.refresh(feed)
        assert feed.last_collected_at is not None
        assert feed.next_run_at > feed.last_collected_at


async def test_custom_detail_paginates_all_history_and_preserves_private_access(client, session):
    user = User(email="pages@anisignal.dev", password_hash="x", is_verified=True)
    session.add(user)
    await session.flush()
    feed = CustomFeed(user_id=user.id, name="历史监控", kind="keyword", target_value="关键词")
    session.add(feed)
    await session.flush()
    session.add_all([
        CustomFeedItem(feed_id=feed.id, platform="bilibili", external_id=f"page-{i}",
                       title=f"历史 {i}", published_at=utcnow() - timedelta(minutes=i))
        for i in range(65)
    ])
    await session.commit()
    await _login(client, session, user.email)
    pages = [(await client.get(f"/api/custom/feeds/{feed.id}", params={"page": p})).json()
             for p in (1, 2, 3)]
    assert [len(page["items"]) for page in pages] == [30, 30, 5]
    assert all(page["total"] == page["item_count"] == 65 for page in pages)
    assert all(page["page_size"] == 30 and page["status"] == "active" for page in pages)
    assert len({item["id"] for page in pages for item in page["items"]}) == 65
    assert (await client.get(f"/api/custom/feeds/{feed.id}?page=0")).status_code == 422
    assert (await client.get(f"/api/custom/feeds/{feed.id}?page_size=101")).status_code == 422
    client.cookies.clear()
    assert (await client.get(f"/api/custom/feeds/{feed.id}?page=2")).status_code == 404


async def test_custom_edit_validates_existing_kind_and_keeps_history(client, session):
    user = User(email="edit@anisignal.dev", password_hash="x", is_verified=True)
    session.add(user)
    await session.flush()
    feed = CustomFeed(user_id=user.id, name="原监控", kind="uid", target_value="123",
                      last_collected_at=utcnow(), next_run_at=utcnow() + timedelta(hours=1))
    session.add(feed)
    await session.flush()
    history = CustomFeedItem(feed_id=feed.id, platform="bilibili", external_id="old")
    session.add(history)
    await session.commit()
    await _login(client, session, user.email)
    endpoint = f"/api/account/custom-feeds/{feed.id}"
    for body in ({"target_value": "abc"}, {"target_value": " "}, {"name": ""},
                 {"min_views": -1}, {"min_views": 10_000_001}, {"min_views": 1.5},
                 {"kind": "keyword"}, {"platform": "youtube"}):
        response = await client.patch(endpoint, json=body)
        assert response.status_code == 400, (body, response.text)
    response = await client.patch(endpoint, json={"name": "新监控", "target_value": "456",
                                                   "is_public": True, "min_views": 500})
    assert response.status_code == 200
    await session.refresh(feed)
    assert (feed.name, feed.target_value, feed.min_views) == ("新监控", "456", 500)
    assert feed.next_run_at is None and feed.last_collected_at is None
    assert await session.get(CustomFeedItem, history.id) is not None
    assert (await client.get("/api/custom/feeds")).json()["total"] == 1
    assert (await client.patch(endpoint, json={"enabled": False})).status_code == 200
    assert (await client.get("/api/custom/feeds")).json()["total"] == 0
    assert (await client.patch(endpoint, json={"enabled": True, "is_public": False})).status_code == 200
    assert (await client.get("/api/custom/feeds")).json()["total"] == 0


async def test_custom_digest_respects_master_switch_and_beijing_day(session, monkeypatch, fake_mail):
    from app.services import custom_feeds

    user = User(email="digest@anisignal.dev", password_hash="x", is_verified=True,
                notification_enabled=False)
    session.add(user)
    await session.flush()
    feed = CustomFeed(user_id=user.id, name="新内容", kind="keyword", target_value="关键词", push_enabled=True)
    session.add(feed)
    await session.flush()
    items = [CustomFeedItem(feed_id=feed.id, platform="bilibili", external_id=f"digest-{i}",
                            title=f"条目 {i}", url=f"https://example.com/{i}") for i in range(2)]
    session.add_all(items)
    await session.commit()
    assert not await custom_feeds.push_custom_feed_digest(session, feed, 2)
    assert await session.scalar(select(NotificationDelivery)) is None
    fake_mail.assert_not_awaited()

    user.notification_enabled = True
    await session.commit()
    monkeypatch.setattr(custom_feeds, "utcnow", lambda: datetime(2026, 10, 11, 15, 59))
    assert await custom_feeds.push_custom_feed_digest(session, feed, 2)
    # 北京时间跨零点可以发送；UTC 零点不应重新计算一个推送日。
    monkeypatch.setattr(custom_feeds, "utcnow", lambda: datetime(2026, 10, 11, 16, 1))
    assert await custom_feeds.push_custom_feed_digest(session, feed, 2)
    monkeypatch.setattr(custom_feeds, "utcnow", lambda: datetime(2026, 10, 12, 0, 1))
    assert not await custom_feeds.push_custom_feed_digest(session, feed, 2)
    rows = list(await session.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id)))
    assert [row.period_date for row in rows] == [date(2026, 10, 11), date(2026, 10, 12)]
    assert rows[1].dedupe_key == f"custom-push:{feed.id}:2026-10-12"
    assert set(rows[1].content_ids) == {item.id for item in items}
    assert "https://example.com/0" in rows[1].body and "北京时间" in rows[1].body
    assert fake_mail.await_count == 2


@pytest.mark.parametrize("kind,expected", [("custom", "skipped"), ("admin", "sent"), ("broadcast", "sent")])
async def test_custom_send_rechecks_current_mail_switch(session, fake_mail, kind, expected):
    from app.services.notifications import send_admin_email

    user = User(email="recheck@anisignal.dev", password_hash="x", is_verified=True, notification_enabled=True)
    session.add(user)
    await session.flush()
    delivery = NotificationDelivery(user_id=user.id, kind=kind, source="schedule",
                                    period_date=date(2026, 10, 11), subject="摘要", body="正文")
    session.add(delivery)
    await session.commit()
    # 模拟另一请求更新开关，当前会话仍缓存旧 User 对象。
    await session.execute(update(User).where(User.id == user.id).values(notification_enabled=False)
                          .execution_options(synchronize_session=False))
    await session.commit()
    assert user.notification_enabled is True
    result = await send_admin_email(session, delivery.id)
    assert result["status"] == expected and delivery.status == expected
    assert fake_mail.await_count == (1 if expected == "sent" else 0)
