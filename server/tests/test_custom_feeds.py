"""定制监控单测：账号 CRUD、公开目录、采集过滤与推送、管理端处置。"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from app.api import main as api_main
from app.clock import utcnow
from app.models import CustomFeed, CustomFeedItem, NotificationDelivery, User
from app.services.auth import hash_password

admin_headers = {"X-Admin-Token": "test-admin-token"}


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

    # 推送已创建（fake send_email 由 send_admin_email 走 SMTP 失败也可，只要投递行落库）
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

    # 删除
    resp = await client.delete(f"/api/admin/custom-feeds/{feed_id}", headers=admin_headers)
    assert resp.status_code == 204
    assert (await client.get("/api/admin/custom-feeds", headers=admin_headers)).json()["total"] == 0
