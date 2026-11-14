"""网页登录协议、凭据保密与升级迁移；不发送真实短信或提交真实账号。"""

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from app.services import bilibili_session as credentials
from app.services import bilibili_web_login as login

CAPTCHA = {"token": "ticket", "challenge": "challenge", "validate": "solved", "seccode": "solved|jordan"}
PASSWORD = "test-password-only-in-memory"
SESSDATA = "private-login-session"


def mock_passport(monkeypatch, handler):
    calls = []

    def respond(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(login, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond), headers=login.HEADERS))
    return calls


def login_response(request, data=None):
    return httpx.Response(200, json={"code": 0, "data": data or {"status": 0}}, headers=[
        ("set-cookie", f"SESSDATA={SESSDATA}; Domain=.bilibili.com; HttpOnly; Path=/"),
        ("set-cookie", "bili_jct=private-csrf; Domain=.bilibili.com; Path=/"),
    ], request=request)


@pytest.fixture
def key_pair():
    private = RSA.generate(2048)
    public = private.public_key().export_key().decode()
    return private, public


async def test_all_new_login_endpoints_require_admin(client):
    for method, path in [("POST", "captcha"), ("GET", "countries"), ("POST", "login/password"), ("POST", "sms/send"), ("POST", "login/sms")]:
        response = await client.request(method, f"/api/admin/bilibili-session/{path}")
        assert response.status_code == 401


async def test_captcha_and_country_parameters(client, admin_headers, monkeypatch):
    def respond(request):
        if request.url.path.endswith("captcha"):
            assert request.url.params["source"] == "main_web"
            return httpx.Response(200, json={"code": 0, "data": {
                "type": "geetest", "token": "ticket", "geetest": {"gt": "gt-id", "challenge": "challenge"},
            }})
        return httpx.Response(200, json={"code": 0, "data": {
            "common": [{"id": 1, "cname": "中国大陆", "country_id": "86"}],
            "others": [{"id": 5, "cname": "中国香港", "country_id": "852"}],
        }})

    mock_passport(monkeypatch, respond)
    response = await client.post("/api/admin/bilibili-session/captcha", headers=admin_headers)
    assert response.json() == {"token": "ticket", "geetest": {"gt": "gt-id", "challenge": "challenge"}}
    response = await client.get("/api/admin/bilibili-session/countries", headers=admin_headers)
    assert response.json() == [{"id": 1, "name": "中国大陆", "code": "86"}, {"id": 5, "name": "中国香港", "code": "852"}]


async def test_password_encrypted_and_only_session_saved(client, admin_headers, session, monkeypatch, key_pair, caplog):
    private, public = key_pair

    def respond(request):
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"code": 0, "data": {"hash": "salt-", "key": public}})
        form = parse_qs(request.content.decode())
        decrypted = PKCS1_v1_5.new(private).decrypt(base64.b64decode(form["password"][0]), None)
        assert decrypted.decode() == "salt-" + PASSWORD
        assert PASSWORD.encode() not in request.content
        assert form["username"] == ["player@example.com"]
        assert form["keep"] == ["true"]
        assert all(form[key] == [value] for key, value in CAPTCHA.items())
        return login_response(request)

    calls = mock_passport(monkeypatch, respond)
    response = await client.post("/api/admin/bilibili-session/login/password", headers=admin_headers,
                                 json={"username": "player@example.com", "password": PASSWORD, **CAPTCHA})
    assert response.status_code == 200 and response.json()["state"] == "saved"
    assert len(calls) == 2
    assert (await credentials.get_db_cookies(session))["SESSDATA"] == SESSDATA
    stored = await credentials.get_setting(session, credentials.CREDENTIAL_SETTING_KEY)
    assert PASSWORD not in json.dumps(stored)
    assert PASSWORD not in response.text + caplog.text
    assert SESSDATA not in response.text + caplog.text


async def test_password_failure_preserves_old_credentials(client, admin_headers, session, monkeypatch, key_pair):
    await credentials.save_login_cookies(session, {"SESSDATA": "old-session"}, "ok")

    def respond(request):
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"code": 0, "data": {"hash": "salt", "key": key_pair[1]}})
        return httpx.Response(200, json={"code": -629, "message": PASSWORD})

    mock_passport(monkeypatch, respond)
    response = await client.post("/api/admin/bilibili-session/login/password", headers=admin_headers,
                                 json={"username": "player@example.com", "password": PASSWORD, **CAPTCHA})
    assert response.status_code == 400
    assert "账号或密码不正确" in response.text and PASSWORD not in response.text
    assert await credentials.get_db_cookies(session) == {"SESSDATA": "old-session"}


async def test_sms_send_then_login(client, admin_headers, session, monkeypatch):
    def respond(request):
        form = parse_qs(request.content.decode())
        assert form["cid"] == ["86"] and form["tel"] == ["13800138000"]
        if request.url.path.endswith("/sms/send"):
            assert all(form[key] == [value] for key, value in CAPTCHA.items())
            return httpx.Response(200, json={"code": 0, "data": {"captcha_key": "sms-ticket"}})
        assert form["code"] == ["123456"] and form["captcha_key"] == ["sms-ticket"]
        return login_response(request)

    mock_passport(monkeypatch, respond)
    sent = await client.post("/api/admin/bilibili-session/sms/send", headers=admin_headers,
                             json={"phone": "13800138000", **CAPTCHA})
    assert sent.status_code == 200 and sent.json()["retry_after"] == 60
    response = await client.post("/api/admin/bilibili-session/login/sms", headers=admin_headers,
                                 json={"cid": 86, "phone": "13800138000", "code": "123456", "captcha_key": sent.json()["captcha_key"]})
    assert response.status_code == 200 and response.json()["state"] == "saved"
    assert SESSDATA not in response.text
    assert "sms-ticket" not in json.dumps(await credentials.get_setting(session, credentials.CREDENTIAL_SETTING_KEY))


async def test_client_carries_buvid3_and_json_cookie_paste():
    async with login._client() as http_client:
        assert "Chrome/" in http_client.headers["User-Agent"]
        assert http_client.cookies.get("buvid3", domain=".bilibili.com", path="/").endswith("infoc")
    assert credentials.parse_cookie_text('{"SESSDATA": "s1", "bili_jct": "c1"}') == {"SESSDATA": "s1", "bili_jct": "c1"}
    assert credentials.parse_cookie_text('[{"name": "SESSDATA", "value": "s2"}]') == {"SESSDATA": "s2"}


@pytest.mark.parametrize("url,expected", [
    ("https://passport.bilibili.com/h5-app/passport/risk/verify?tmp_token=private", True),
    ("https://attacker.example/verify", False),
])
async def test_extra_verification_never_overwrites_old_session(client, admin_headers, session, monkeypatch, url, expected):
    await credentials.save_login_cookies(session, {"SESSDATA": "old-session"}, "ok")
    mock_passport(monkeypatch, lambda request: login_response(request, {"status": 2, "url": url}))
    response = await client.post("/api/admin/bilibili-session/login/sms", headers=admin_headers,
                                 json={"phone": "13800138000", "code": "123456", "captcha_key": "sms-ticket"})
    assert response.status_code == 200 and response.json()["state"] == "verification_required"
    assert bool(response.json()["verification_url"]) is expected
    assert await credentials.get_db_cookies(session) == {"SESSDATA": "old-session"}


async def test_login_does_not_submit_without_completed_captcha(client, admin_headers, monkeypatch):
    calls = mock_passport(monkeypatch, lambda request: pytest.fail("不应向B站发起请求"))
    response = await client.post("/api/admin/bilibili-session/login/password", headers=admin_headers,
                                 json={"username": "player@example.com", "password": PASSWORD})
    assert response.status_code == 422 and not calls


async def test_network_error_does_not_disclose_upstream_data(client, admin_headers, monkeypatch):
    def respond(request):
        raise httpx.ConnectError(PASSWORD + SESSDATA, request=request)

    mock_passport(monkeypatch, respond)
    response = await client.post("/api/admin/bilibili-session/captcha", headers=admin_headers)
    assert response.status_code == 502
    assert PASSWORD not in response.text and SESSDATA not in response.text


async def test_file_migration_is_idempotent_and_fixes_unreadable_mount(session, tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text("SESSDATA=legacy-session; bili_jct=legacy-csrf", encoding="utf-8")
    result = await credentials.import_file_credentials(session, str(path))
    assert result["state"] == "imported" and "legacy-session" not in json.dumps(result)
    path.write_text("SESSDATA=another-session", encoding="utf-8")
    assert (await credentials.import_file_credentials(session, str(path)))["state"] == "skipped"

    def unreadable(_path):
        raise PermissionError

    monkeypatch.setattr(credentials, "read_cookie_file", unreadable)
    state, cookies = await credentials.load_credentials(session)
    assert state["source"] == "database" and cookies["SESSDATA"] == "legacy-session"


async def test_invalid_file_migration_preserves_database(session, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("not a cookie", encoding="utf-8")
    with pytest.raises(ValueError):
        await credentials.import_file_credentials(session, str(path))
    assert await credentials.get_db_cookies(session) == {}


async def test_validation_error_does_not_echo_password_or_code(client, admin_headers):
    """F51：缺字段的 422 不能把请求体里的密码、短信验证码原样回显。"""
    secret = "Fake-Pass-For-Test-9z"
    resp = await client.post(
        "/api/admin/bilibili-session/login/password",
        headers=admin_headers,
        json={"username": "someone", "password": secret},
    )
    assert resp.status_code == 422
    assert secret not in resp.text
    assert all("input" not in err for err in resp.json()["detail"])
    assert {tuple(err["loc"]) for err in resp.json()["detail"]} >= {("body", "token")}

    resp = await client.post(
        "/api/admin/bilibili-session/login/sms",
        headers=admin_headers,
        json={"phone": "abc", "code": "778899", "captcha_key": "fake-captcha-key"},
    )
    assert resp.status_code == 422
    assert "778899" not in resp.text and "fake-captcha-key" not in resp.text
