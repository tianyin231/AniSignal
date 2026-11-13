"""B站网页账密/短信登录：人机验证由用户完成，密码只用于当次请求。"""

import base64
from urllib.parse import urlsplit

import httpx
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from app.services.bilibili_session import login_response_cookies, save_login_cookies

PASSPORT = "https://passport.bilibili.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://passport.bilibili.com/login", "Origin": PASSPORT}
ERRORS = {
    -105: "人机验证未通过，请重新验证",
    -629: "B站账号或密码不正确",
    -653: "请输入B站账号和密码",
    -662: "登录验证已过期，请重新验证",
    -2100: "B站要求验证手机号或邮箱，请在官方页面完成验证，或改用短信/扫码登录",
    1002: "手机号码格式不正确",
    1003: "验证码已经发送，请稍后再试",
    1006: "短信验证码不正确",
    1007: "短信验证码已过期，请重新获取",
    2400: "登录验证已过期，请重新验证",
    2406: "人机验证未通过，请重新验证",
    86203: "短信发送次数已达上限，请稍后再试",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15, trust_env=False, headers=HEADERS)


def _data(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        code = payload.get("code")
        # 不把上游原始响应、密码、验证码或会话 URL 带进错误信息。
        raise ValueError(ERRORS.get(code, "B站暂未接受此次登录，请稍后重试或改用扫码登录"))
    return payload.get("data") or {}


async def create_captcha() -> dict:
    async with _client() as client:
        data = _data(await client.get(PASSPORT + "/x/passport-login/captcha", params={"source": "main_web"}))
    geetest = data.get("geetest") or {}
    if data.get("type") != "geetest" or not all((data.get("token"), geetest.get("gt"), geetest.get("challenge"))):
        raise ValueError("B站暂未提供可用的人机验证，请改用扫码登录")
    return {"token": data["token"], "geetest": {"gt": geetest["gt"], "challenge": geetest["challenge"]}}


async def country_list() -> list[dict]:
    async with _client() as client:
        data = _data(await client.get(PASSPORT + "/web/generic/country/list"))
    return [
        {"id": item["id"], "name": item["cname"], "code": item["country_id"]}
        for item in [*data.get("common", []), *data.get("others", [])]
    ]


async def _finish_login(session, client: httpx.AsyncClient, data: dict) -> dict:
    if data.get("status") != 0:
        url = str(data.get("url") or "")
        parsed = urlsplit(url)
        # 只允许打开B站官方验证页，成功响应中的跨站 SSO URL 不对外返回。
        verification_url = url if parsed.scheme == "https" and parsed.netloc == "passport.bilibili.com" else None
        return {
            "state": "verification_required",
            "message": "B站要求额外安全验证，请在官方页面完成后重新登录，或改用扫码登录",
            "verification_url": verification_url,
        }
    return await save_login_cookies(session, login_response_cookies(client), "B站登录成功，凭据已保存")


async def login_password(session, username: str, password: str, captcha: dict) -> dict:
    async with _client() as client:
        key_data = _data(await client.get(PASSPORT + "/x/passport-login/web/key"))
        try:
            key = RSA.import_key(key_data["key"])
            encrypted = PKCS1_v1_5.new(key).encrypt((key_data["hash"] + password).encode())
        except (KeyError, ValueError, TypeError):
            raise ValueError("B站登录密钥不可用或密码过长，请重新登录") from None
        data = _data(await client.post(PASSPORT + "/x/passport-login/web/login", data={
            "username": username, "password": base64.b64encode(encrypted).decode(),
            "keep": "0", "source": "main_web", **captcha,
        }))
        return await _finish_login(session, client, data)


async def send_sms(cid: int, phone: str, captcha: dict) -> dict:
    async with _client() as client:
        data = _data(await client.post(PASSPORT + "/x/passport-login/web/sms/send", data={
            "cid": str(cid), "tel": phone, "source": "main_web", **captcha,
        }))
    if not data.get("captcha_key"):
        raise ValueError("B站未返回短信登录凭据，请重新获取验证码")
    return {"captcha_key": data["captcha_key"], "retry_after": 60, "message": "短信验证码已发送"}


async def login_sms(session, cid: int, phone: str, code: str, captcha_key: str) -> dict:
    async with _client() as client:
        data = _data(await client.post(PASSPORT + "/x/passport-login/web/login/sms", data={
            "cid": str(cid), "tel": phone, "code": code, "captcha_key": captcha_key,
            "source": "main_web", "keep": "true",
        }))
        return await _finish_login(session, client, data)
