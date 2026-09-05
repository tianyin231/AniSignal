"""Admin 用户管理单测：快查分页、封禁/解封、订阅开关、单发与广播邮件。"""

import pytest
from sqlalchemy import select

from app.api import main as api_main
from app.models import NotificationDelivery, User, UserSession
from app.clock import utcnow

admin_headers = {"X-Admin-Token": "test-admin-token"}


async def _seed_users(session, n: int = 3) -> list[User]:
    users = []
    for i in range(n):
        user = User(
            email=f"user{i}@anisignal.dev",
            display_name=f"用户{i}",
            password_hash="x",
            is_verified=True,
            notification_enabled=True,
        )
        session.add(user)
        users.append(user)
    await session.commit()
    return users


@pytest.fixture
def no_smtp(monkeypatch):
    """默认无 SMTP：发邮件端点应 400 快速失败。"""


async def test_admin_users_list_search_pagination(client, session):
    await _seed_users(session, 5)
    resp = await client.get("/api/admin/users", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5

    resp = await client.get(
        "/api/admin/users", params={"q": "user1@"}, headers=admin_headers
    )
    items = resp.json()["items"]
    assert resp.json()["total"] == 1 and items[0]["email"] == "user1@anisignal.dev"

    # 分页：page_size=2 → 两页
    p1 = await client.get("/api/admin/users", params={"page_size": 2}, headers=admin_headers)
    p2 = await client.get(
        "/api/admin/users", params={"page_size": 2, "page": 2}, headers=admin_headers
    )
    ids1 = {u["id"] for u in p1.json()["items"]}
    ids2 = {u["id"] for u in p2.json()["items"]}
    assert ids1.isdisjoint(ids2) and len(ids1) == 2


async def test_admin_ban_flow(client, session):
    from app.services.auth import hash_password

    (user,) = await _seed_users(session, 1)
    user.password_hash = hash_password("anisignal123")
    await session.commit()
    # 正常登录
    resp = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "anisignal123"}
    )
    assert resp.status_code == 200
    token = "seeded-session-token"
    import hashlib

    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=utcnow().replace(tzinfo=None) + __import__("datetime").timedelta(days=1),
        )
    )
    await session.commit()

    resp = await client.patch(
        f"/api/admin/users/{user.id}",
        json={"banned": True, "ban_reason": "违规刷帖"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "banned"
    # 会话即时吊销
    left = await session.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert left is None

    # 封禁后正确密码也拒绝登录
    resp = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "anisignal123"}
    )
    assert resp.status_code == 403

    # 解封
    resp = await client.patch(
        f"/api/admin/users/{user.id}", json={"banned": False}, headers=admin_headers
    )
    assert resp.json()["status"] == "active"
    fresh = await session.get(User, user.id)
    assert fresh.banned_reason is None and fresh.banned_at is None


async def test_admin_user_detail_and_subscription(client, session):
    from app.models import UserKeyword

    (user,) = await _seed_users(session, 1)
    session.add(UserKeyword(user_id=user.id, keyword="爱弥斯"))
    await session.commit()

    detail = (await client.get(f"/api/admin/users/{user.id}", headers=admin_headers)).json()
    assert detail["keyword_count"] == 1
    assert [k["keyword"] for k in detail["keywords"]] == ["爱弥斯"]

    resp = await client.patch(
        f"/api/admin/users/{user.id}", json={"notification_enabled": False}, headers=admin_headers
    )
    assert resp.json()["notification_enabled"] is False


async def test_admin_email_requires_smtp_and_queues(client, session, monkeypatch):
    (user,) = await _seed_users(session, 1)
    # 无 SMTP → 400
    resp = await client.post(
        f"/api/admin/users/{user.id}/email",
        json={"subject": "提醒", "body": "请注意违规行为"},
        headers=admin_headers,
    )
    assert resp.status_code == 400

    # 配置 SMTP（绕过真实发送：queue 是 fake）
    async def fake_smtp(_session):
        return {"host": "smtp.test", "port": 465, "user": "u", "password": "p", "from": "a@b.c", "tls_mode": "tls"}

    monkeypatch.setattr("app.api.admin_users.get_effective_smtp", fake_smtp)
    resp = await client.post(
        f"/api/admin/users/{user.id}/email",
        json={"subject": "违规提醒", "body": "请注意社区行为规范"},
        headers=admin_headers,
    )
    assert resp.status_code == 202
    fake = api_main.app.state.queue
    assert fake.admin_emails == [resp.json()["delivery_id"]]
    row = await session.get(NotificationDelivery, resp.json()["delivery_id"])
    assert row.kind == "admin" and row.subject == "违规提醒"

    # 空主题 400
    resp = await client.post(
        f"/api/admin/users/{user.id}/email",
        json={"subject": "", "body": "x"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


async def test_admin_broadcast_chunks(client, session, monkeypatch):
    await _seed_users(session, 7)

    async def fake_smtp(_session):
        return {"host": "smtp.test", "port": 465, "user": "u", "password": "p", "from": "a@b.c", "tls_mode": "tls"}

    monkeypatch.setattr("app.api.admin_users.get_effective_smtp", fake_smtp)
    fake = api_main.app.state.queue
    fake.admin_emails.clear()

    # 无 confirm → 400
    resp = await client.post(
        "/api/admin/users/broadcast",
        json={"subject": "全站公告", "body": "新功能上线"},
        headers=admin_headers,
    )
    assert resp.status_code == 400

    resp = await client.post(
        "/api/admin/users/broadcast",
        json={"subject": "全站公告", "body": "新功能上线", "confirm": True},
        headers=admin_headers,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["targets"] == 7 and data["queued"] == 7
    # 7 人按 50/块 → 1 个批次任务
    assert data["batches"] == 1
    assert len(fake.admin_emails) == 7
    rows = list((await session.execute(select(NotificationDelivery))).scalars())
    assert all(d.kind == "broadcast" for d in rows)
    # 封禁用户不收广播
    resp = await client.patch(
        f"/api/admin/users/{rows[0].user_id}", json={"banned": True}, headers=admin_headers
    )
    assert resp.status_code == 200
