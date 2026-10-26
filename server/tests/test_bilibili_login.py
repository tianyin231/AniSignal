import os
import stat

import httpx
import pytest

from app import bilibili_login as login
from app.services.bilibili_session import read_cookie_file


async def test_qr_login_only_saves_confirmed_cookies(tmp_path, monkeypatch, capsys):
    path, image = tmp_path / "private" / "cookies.txt", tmp_path / "qr.png"
    path.parent.mkdir()
    path.write_text("SESSDATA=previous-session", encoding="utf-8")
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("generate"):
            return httpx.Response(200, json={"code": 0, "data": {"url": "https://passport.bilibili.com/login/scan?key=test", "qrcode_key": "test"}})
        assert read_cookie_file(str(path))["SESSDATA"] == "previous-session"
        if len(calls) == 2:
            return httpx.Response(200, json={"code": 0, "data": {"code": 86090}})
        return httpx.Response(200, json={"code": 0, "data": {"code": 0, "refresh_token": "never-store"}}, headers=[
            ("set-cookie", "SESSDATA=authorized-session; Domain=.bilibili.com; Path=/; Secure; HttpOnly"),
            ("set-cookie", "bili_jct=private-csrf; Domain=.bilibili.com; Path=/; Secure"),
        ])

    real_client = httpx.AsyncClient
    monkeypatch.setattr(login.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))

    async def sleep(_):
        pass

    monkeypatch.setattr(login.asyncio, "sleep", sleep)
    assert await login.login(path, image)
    assert read_cookie_file(str(path)) == {"SESSDATA": "authorized-session", "bili_jct": "private-csrf"}
    output = capsys.readouterr().out
    assert "scanned" in output and "saved" in output
    assert all(secret not in output + path.read_text() for secret in ("never-store",))
    assert "authorized-session" not in output and "private-csrf" not in output
    assert not image.exists()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_expired_qr_keeps_existing_session(tmp_path, monkeypatch):
    path, image = tmp_path / "cookies.txt", tmp_path / "qr.png"
    path.write_text("SESSDATA=previous-session", encoding="utf-8")

    def respond(request):
        return httpx.Response(200, json={"code": 0, "data": {"url": "https://passport.bilibili.com/", "qrcode_key": "test", "code": 86038}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(login.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))

    async def sleep(_):
        pass

    monkeypatch.setattr(login.asyncio, "sleep", sleep)
    assert not await login.login(path, image)
    assert path.read_text() == "SESSDATA=previous-session"
    assert not image.exists()


def test_missing_sessdata_never_overwrites_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("SESSDATA=previous-session", encoding="utf-8")
    with pytest.raises(ValueError):
        login.save_cookies(path, {"bili_jct": "not-a-session"})
    assert path.read_text() == "SESSDATA=previous-session"
