"""登录健康与凭据保密：会话失效、风控、更新文件均有独立语义。"""

import json

import httpx
import pytest

from app.config import get_settings
from app.services import bilibili_session as auth


@pytest.fixture
def cookie_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text("SESSDATA=private%2Csession; bili_jct=private-csrf; DedeUserID=123456789", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "bili_cookie_file", str(path))
    return path


def mock_nav(monkeypatch, payload, status=200):
    real_client = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    return requests


def test_cli_file_formats_and_masking(cookie_file):
    cookies = auth.read_cookie_file(str(cookie_file))
    assert cookies["SESSDATA"] == "private%2Csession"
    cookie_file.write_text("# Netscape\n.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tprivate%2Csession\n", encoding="utf-8")
    assert auth.read_cookie_file(str(cookie_file)) == {"SESSDATA": "private%2Csession"}
    masked = auth.redact("SESSDATA=private%2Csession private,session private-csrf DedeUserID=123456789", cookies)
    assert all(secret not in masked for secret in ("private", "123456789"))
    cookie_file.write_text("not a cookie", encoding="utf-8")
    with pytest.raises(ValueError, match="SESSDATA"):
        auth.read_cookie_file(str(cookie_file))


@pytest.mark.parametrize("payload,status,expected", [
    ({"code": 0, "data": {"isLogin": True, "uname": "private-name"}}, 200, "logged_in"),
    ({"code": -101}, 200, "expired"),
    ({"code": 0, "data": {"isLogin": False}}, 200, "expired"),
    ({"code": -352}, 200, "unknown"),
    ({}, 412, "unknown"),
])
async def test_login_is_distinguished_from_restriction(session, monkeypatch, cookie_file, payload, status, expected):
    calls = mock_nav(monkeypatch, payload, status)
    result = await auth.check_status(session)
    assert result["state"] == expected
    assert calls[0].headers["cookie"].startswith("SESSDATA=private%2Csession")
    assert "private" not in json.dumps(result)
    assert await auth.get_status(session) == result
    assert "private" not in json.dumps(await auth.get_setting(session, auth.SETTING_KEY))


async def test_new_credentials_invalidate_old_login_status(session, monkeypatch, cookie_file):
    mock_nav(monkeypatch, {"code": -101})
    old = await auth.check_status(session)
    cookie_file.write_text("SESSDATA=new-session", encoding="utf-8")
    new = await auth.get_status(session)
    assert old["state"] == "expired" and new["state"] == "unchecked"
    assert old["credential_version"] != new["credential_version"]


async def test_missing_credentials_do_not_make_network_calls(session, monkeypatch, cookie_file):
    cookie_file.unlink()
    calls = mock_nav(monkeypatch, {})
    assert (await auth.check_status(session))["state"] == "missing"
    assert calls == []


async def test_admin_only_status_and_check(client, admin_headers, cookie_file, monkeypatch):
    mock_nav(monkeypatch, {"code": 0, "data": {"isLogin": True}})
    assert (await client.get("/api/admin/bilibili-session")).status_code in (401, 422)
    response = await client.post("/api/admin/bilibili-session/check", headers=admin_headers)
    assert response.status_code == 200 and response.json()["state"] == "logged_in"
    status = await client.get("/api/admin/bilibili-session", headers=admin_headers)
    assert status.json() == response.json()
    assert "private" not in status.text
