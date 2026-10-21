"""账号系统全流程单测：注册→验证→登录→改密→找回，含安全边界。"""

from collections import deque

import pytest
from sqlalchemy import select

from app.models import EmailCode, User


async def _latest_code(session, email: str, purpose: str = "register") -> str:
    code_row = await session.scalar(
        select(EmailCode)
        .join(User, EmailCode.user_id == User.id)
        .where(User.email == email, EmailCode.purpose == purpose)
        .order_by(EmailCode.id.desc())
        .limit(1)
    )
    assert code_row is not None, "验证码未签发"
    assert code_row.code_plain, "SMTP 未配置时应保留明码"
    return code_row.code_plain


async def test_full_register_login_flow(client, session):
    # 注册
    resp = await client.post(
        "/api/auth/register", json={"email": "User@Example.com ", "password": "password123"}
    )
    assert resp.status_code == 200
    # 邮箱归一化为小写
    user = await session.scalar(select(User).where(User.email == "user@example.com"))
    assert user is not None and not user.is_verified

    # 未验证登录被拒
    resp = await client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    assert resp.status_code == 403

    # 验证码错误
    code = await _latest_code(session, "user@example.com")
    resp = await client.post(
        "/api/auth/verify", json={"email": "user@example.com", "code": "000000"}
    )
    assert resp.status_code == 400

    # 正确验证 → 自动登录
    resp = await client.post(
        "/api/auth/verify", json={"email": "user@example.com", "code": code}
    )
    assert resp.status_code == 200
    assert "anisignal_session" in resp.cookies

    # me
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "user@example.com"

    # 登出后再 me → 401
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_login_wrong_password_and_lockout(client, session):
    await client.post(
        "/api/auth/register", json={"email": "a@b.com", "password": "password123"}
    )
    code = await _latest_code(session, "a@b.com")
    await client.post("/api/auth/verify", json={"email": "a@b.com", "code": code})
    await client.post("/api/auth/logout")

    for _ in range(5):
        resp = await client.post(
            "/api/auth/login", json={"email": "a@b.com", "password": "wrong-pass"}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "邮箱或密码不正确"  # 不泄露具体原因

    # 第 6 次即使密码正确也被锁
    resp = await client.post(
        "/api/auth/login", json={"email": "a@b.com", "password": "password123"}
    )
    assert resp.status_code == 429


async def test_change_password_revokes_sessions(client, session):
    await client.post("/api/auth/register", json={"email": "c@d.com", "password": "password123"})
    code = await _latest_code(session, "c@d.com")
    await client.post("/api/auth/verify", json={"email": "c@d.com", "code": code})

    # 建第二个会话（模拟另一设备）：直接登录再退出前保存 cookie
    resp = await client.post("/api/auth/login", json={"email": "c@d.com", "password": "password123"})
    assert resp.status_code == 200
    other_cookie = resp.cookies.get("anisignal_session")

    # 改密码（旧密码错误被拒）
    resp = await client.post(
        "/api/auth/password/change",
        json={"old_password": "nope1234", "new_password": "newpassword1"},
    )
    assert resp.status_code == 400

    resp = await client.post(
        "/api/auth/password/change",
        json={"old_password": "password123", "new_password": "newpassword1"},
    )
    assert resp.status_code == 200

    # 旧会话（另一设备）失效
    resp = await client.get(
        "/api/auth/me", cookies={"anisignal_session": other_cookie}
    )
    assert resp.status_code == 401

    # 新密码可登录
    resp = await client.post("/api/auth/login", json={"email": "c@d.com", "password": "newpassword1"})
    assert resp.status_code == 200


async def test_password_reset_flow(client, session):
    await client.post("/api/auth/register", json={"email": "e@f.com", "password": "password123"})
    code = await _latest_code(session, "e@f.com")
    await client.post("/api/auth/verify", json={"email": "e@f.com", "code": code})
    resp = await client.post("/api/auth/login", json={"email": "e@f.com", "password": "password123"})
    old_cookie = resp.cookies.get("anisignal_session")

    # 忘记密码 → 发重置码（清掉注册阶段的发码限频，模拟真实时间间隔）
    from app.services.auth import _send_times

    _send_times.clear()
    resp = await client.post("/api/auth/password/forgot", json={"email": "e@f.com"})
    assert resp.status_code == 200
    reset_code = await _latest_code(session, "e@f.com", purpose="reset")

    # 重置
    resp = await client.post(
        "/api/auth/password/reset",
        json={"email": "e@f.com", "code": reset_code, "new_password": "resetpass1"},
    )
    assert resp.status_code == 200

    # 全部旧会话失效 + 新密码可登录
    resp = await client.get("/api/auth/me", cookies={"anisignal_session": old_cookie})
    assert resp.status_code == 401
    resp = await client.post("/api/auth/login", json={"email": "e@f.com", "password": "resetpass1"})
    assert resp.status_code == 200


async def test_register_does_not_leak_existing_account(client, session):
    await client.post("/api/auth/register", json={"email": "x@y.com", "password": "password123"})
    code = await _latest_code(session, "x@y.com")
    await client.post("/api/auth/verify", json={"email": "x@y.com", "code": code})

    # 已存在邮箱再注册：响应与成功一致（防枚举）
    resp = await client.post("/api/auth/register", json={"email": "x@y.com", "password": "another123"})
    assert resp.status_code == 200
    # 未存在邮箱忘记密码：同样文案
    resp = await client.post("/api/auth/password/forgot", json={"email": "ghost@y.com"})
    assert resp.status_code == 200


async def test_password_policy_and_weak_passwords(client):
    resp = await client.post("/api/auth/register", json={"email": "bad", "password": "password123"})
    assert resp.status_code == 400
    resp = await client.post("/api/auth/register", json={"email": "ok@ok.com", "password": "short"})
    assert resp.status_code == 400


async def test_send_rate_limit(client, session):
    for i in range(5):
        resp = await client.post(
            "/api/auth/register",
            json={"email": f"rate{i}@r.com" if i == 0 else "rate0@r.com", "password": "password123"},
        )
        if resp.status_code == 429:
            break
        # 同邮箱连续触发限频（间隔为 0）
    resp = await client.post(
        "/api/auth/register", json={"email": "rate0@r.com", "password": "password123"}
    )
    assert resp.status_code == 429


# ---------- 限频表封顶与淘汰（内存 DoS 与「解锁原语」） ----------


def _reset_state():
    from app.services import auth as auth_module

    auth_module._send_times.clear()
    auth_module._login_failures.clear()
    auth_module._state_ops = 0


def test_login_failure_table_is_bounded():
    """未认证刷随机邮箱不能无限涨内存。"""
    from app.services import auth as auth_module

    _reset_state()
    try:
        for i in range(auth_module._MAX_TRACKED_KEYS + 2000):
            auth_module.record_login_failure(f"flood{i}@example.com")
        assert len(auth_module._login_failures) <= auth_module._MAX_TRACKED_KEYS + auth_module._STATE_OPS_PER_SWEEP
    finally:
        _reset_state()


def test_send_limit_table_is_bounded():
    """注册发码那张表同样按邮箱增长，必须一起封顶。"""
    from app.services import auth as auth_module

    _reset_state()
    try:
        for i in range(auth_module._MAX_TRACKED_KEYS + 2000):
            auth_module._check_send_limit(f"flood{i}@example.com")
        assert len(auth_module._send_times) <= auth_module._MAX_TRACKED_KEYS + auth_module._STATE_OPS_PER_SWEEP
    finally:
        _reset_state()


def test_flood_does_not_unlock_victim():
    """淘汰策略必须保护「正在锁定」的条目，否则刷随机邮箱就是解锁原语。"""
    from app.services import auth as auth_module

    _reset_state()
    try:
        victim = "victim@example.com"
        for _ in range(auth_module._LOGIN_FAIL_LIMIT):
            auth_module.record_login_failure(victim)

        with pytest.raises(auth_module.AuthError):
            auth_module._check_login_lock(victim)

        for i in range(auth_module._MAX_TRACKED_KEYS * 2):
            auth_module.record_login_failure(f"flood{i}@example.com")

        # 受害者仍被锁：攻击者拿不到额外 5 次口令尝试
        with pytest.raises(auth_module.AuthError):
            auth_module._check_login_lock(victim)
    finally:
        _reset_state()


def test_expired_entries_are_swept():
    from app.services import auth as auth_module

    _reset_state()
    try:
        stale = "stale@example.com"
        auth_module._login_failures[stale] = deque([0.0])
        auth_module._maybe_sweep(force=True)
        assert stale not in auth_module._login_failures
    finally:
        _reset_state()


async def test_full_locked_table_rejects_new_keys_and_recovers(client, monkeypatch):
    """全部条目都已锁定时，新增邮箱不能继续占内存，也不能挤掉已有锁定。"""
    from app.services import auth as auth_module

    _reset_state()
    monkeypatch.setattr(auth_module, "_MAX_TRACKED_KEYS", 8)
    monkeypatch.setattr(auth_module, "_STATE_OPS_PER_SWEEP", 32)
    capacity = auth_module._MAX_TRACKED_KEYS + auth_module._STATE_OPS_PER_SWEEP
    now = auth_module.time.monotonic()
    try:
        for i in range(capacity):
            auth_module._login_failures[f"locked{i}@example.com"] = deque(
                [now] * auth_module._LOGIN_FAIL_LIMIT
            )

        for i in range(5):
            response = await client.post(
                "/api/auth/login",
                json={"email": f"overflow{i}@example.com", "password": "wrong-password"},
            )
            assert response.status_code == 429
        assert len(auth_module._login_failures) == capacity
        with pytest.raises(auth_module.AuthError):
            auth_module._check_login_lock("locked0@example.com")

        for times in auth_module._login_failures.values():
            times.clear()
            times.append(now - auth_module._LOGIN_FAIL_WINDOW - 1)
        auth_module._maybe_sweep(force=True)
        response = await client.post(
            "/api/auth/login",
            json={"email": "recovered@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert len(auth_module._login_failures) == 1
    finally:
        _reset_state()
