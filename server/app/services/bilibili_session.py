"""B站授权会话：支持管理端扫码/粘贴 Cookie 落库（优先）及本地只读凭据文件兜底。"""

import base64
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from app.clock import utcnow
from app.config import get_settings
from app.services.settings import get_setting, set_setting

logger = logging.getLogger(__name__)
SETTING_KEY = "bilibili_session_status"
CREDENTIAL_SETTING_KEY = "bilibili_credentials"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
PASSPORT_QR_BASE = "https://passport.bilibili.com/x/passport-login/web/qrcode/"


def parse_cookie_text(content: str) -> dict[str, str]:
    """解析纯 Cookie 请求头、Netscape 文本或 JSON 对象/数组；必须包含 SESSDATA。"""
    stripped = (content or "").strip()
    cookies: dict[str, str] = {}
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            src = parsed.get("cookies") if isinstance(parsed.get("cookies"), dict) else parsed
            cookies = {
                str(k): str(v)
                for k, v in src.items()
                if re.fullmatch(r"[A-Za-z0-9_]+", str(k)) and str(v)
            }
        elif isinstance(parsed, list):
            cookies = {
                str(item["name"]): str(item["value"])
                for item in parsed
                if isinstance(item, dict)
                and re.fullmatch(r"[A-Za-z0-9_]+", str(item.get("name") or ""))
                and str(item.get("value") or "")
            }
    if not cookies:
        parts = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("cookie:"):
                line = line[7:].strip()
            fields = line.split("\t")
            parts.append(f"{fields[5]}={fields[6]}" if len(fields) == 7 else line)
        for part in "; ".join(parts).split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and re.fullmatch(r"[A-Za-z0-9_]+", name) and value:
                cookies[name] = value
    if not cookies.get("SESSDATA"):
        raise ValueError("B站凭据文件缺少 SESSDATA，请重新登录")
    return cookies


def read_cookie_file(path: str) -> dict[str, str]:
    """与 bili 的纯请求头/Netscape 文件格式一致；不从其他进程环境隐式取凭据。"""
    if not path:
        return {}
    content = Path(path).read_text(encoding="utf-8-sig")
    return parse_cookie_text(content)


def cookie_header(cookies: dict) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def credential_version(cookies: dict) -> str:
    return hashlib.sha256(cookie_header(cookies).encode()).hexdigest()[:12] if cookies else ""


def redact(text: str, cookies: dict) -> str:
    # 先替换完整请求头与键值，再遮蔽长度足够的独立敏感值，避免 0/1 等短配置项污染普通数字报错。
    if cookies:
        text = text.replace(cookie_header(cookies), "[B站凭据已隐藏]")
        names = "|".join(re.escape(k) for k in cookies if k)
        if names:
            text = re.sub(rf"\b({names})\s*=\s*[^;\s\"']+", r"\1=***", text)
    secrets = {
        variant
        for value in cookies.values()
        for variant in (value, unquote(value), quote(value, safe=""))
        if len(variant) >= 6
    }
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return re.sub(r"(?i)\b(SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|sid|buvid[34])\s*=\s*[^;\s\"']+", r"\1=***", text)


# 未登录不是故障：匿名即可采关键词与 UP 主投稿，登录只是提高动态接口成功率。
ANONYMOUS_HINT = "未登录B站（可选）：匿名即可采集关键词与UP主投稿，登录后动态接口成功率更高"


def local_status() -> tuple[dict, dict]:
    path = get_settings().bili_cookie_file
    state = {"state": "unconfigured", "message": ANONYMOUS_HINT, "credential_version": ""}
    if not path:
        return state, {}
    try:
        cookies = read_cookie_file(path)
    except FileNotFoundError:
        return {**state, "state": "missing", "message": ANONYMOUS_HINT}, {}
    except (OSError, ValueError, UnicodeError):
        return {**state, "state": "invalid", "message": "B站凭据文件不可用，请重新登录"}, {}
    return {**state, "state": "unchecked", "message": "B站凭据已就绪，等待检测", "credential_version": credential_version(cookies)}, cookies


async def get_db_cookies(session) -> dict[str, str]:
    if session is None:
        return {}
    stored = await get_setting(session, CREDENTIAL_SETTING_KEY)
    if isinstance(stored, dict) and isinstance(stored.get("cookies"), dict):
        cookies = {
            str(k): str(v)
            for k, v in stored["cookies"].items()
            if re.fullmatch(r"[A-Za-z0-9_]+", str(k)) and str(v)
        }
        if cookies.get("SESSDATA"):
            return cookies
    return {}


async def load_credentials(session) -> tuple[dict, dict[str, str]]:
    db_cookies = await get_db_cookies(session)
    if db_cookies:
        return (
            {
                "state": "unchecked",
                "message": "B站凭据已就绪，等待检测",
                "credential_version": credential_version(db_cookies),
                "source": "database",
            },
            db_cookies,
        )
    return local_status()


async def import_file_credentials(session, path: str) -> dict:
    """升级到非 root 容器前/后，以有读取权限的用户执行一次；已有后台凭据不覆盖。"""
    if await get_db_cookies(session):
        return {"state": "skipped", "message": "后台已有B站凭据，未覆盖"}
    cookies = read_cookie_file(path)
    if not cookies.get("SESSDATA"):
        raise ValueError("凭据文件中没有B站登录态")
    await set_setting(session, CREDENTIAL_SETTING_KEY, {"cookies": cookies, "updated_at": utcnow().isoformat()})
    await set_setting(session, SETTING_KEY, {})
    return {"state": "imported", "message": "B站凭据已迁入后台，可在采集目标页检测登录态"}


async def get_status(session) -> dict:
    state, _ = await load_credentials(session)
    stored = await get_setting(session, SETTING_KEY) or {}
    if state["state"] != "unchecked" or stored.get("credential_version") != state["credential_version"]:
        return state
    return stored or state


async def check_status(session) -> dict:
    state, cookies = await load_credentials(session)
    if cookies:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
                "Cookie": cookie_header(cookies),
            }) as client:
                response = await client.get(NAV_URL)
                response.raise_for_status()
                payload = response.json()
            if payload.get("code") == -101 or payload.get("code") == 0 and not (payload.get("data") or {}).get("isLogin"):
                state.update(state="expired", message="B站登录态已失效，请重新扫码登录")
            elif payload.get("code") == 0 and (payload.get("data") or {}).get("isLogin"):
                state.update(state="logged_in", message="B站登录态有效")
            else:
                state.update(state="unknown", message="B站暂时拒绝状态检测，保留凭据并稍后复查")
        except (httpx.HTTPError, ValueError):
            state.update(state="unknown", message="B站状态检测暂时失败，保留凭据并稍后复查")
    state["checked_at"] = utcnow().isoformat()
    previous = await get_setting(session, SETTING_KEY) or {}
    await set_setting(session, SETTING_KEY, state)
    if state["state"] in {"expired", "invalid", "missing"} and previous.get("state") != state["state"]:
        logger.warning("%s", state["message"])
    return state


async def save_login_cookies(session, cookies: dict[str, str], message: str) -> dict:
    """各登录方式共用凭据落库；失败时不覆盖现有会话。"""
    if not cookies.get("SESSDATA"):
        raise ValueError("登录响应缺少会话凭据，请重试")
    await set_setting(session, CREDENTIAL_SETTING_KEY, {"cookies": cookies, "updated_at": utcnow().isoformat()})
    state = {
        "state": "logged_in", "message": "B站登录态有效", "source": "database",
        "credential_version": credential_version(cookies), "checked_at": utcnow().isoformat(),
    }
    await set_setting(session, SETTING_KEY, state)
    return {"state": "saved", "message": message, "session": state}


def login_response_cookies(client: httpx.AsyncClient) -> dict[str, str]:
    return {
        cookie.name: cookie.value for cookie in client.cookies.jar
        if cookie.domain.lstrip(".") in {"bilibili.com", "passport.bilibili.com"}
        and re.fullmatch(r"[A-Za-z0-9_]+", cookie.name) and cookie.value
    }


async def generate_qr_login() -> dict:
    import qrcode

    async with httpx.AsyncClient(
        timeout=15,
        trust_env=False,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
    ) as client:
        response = await client.get(PASSPORT_QR_BASE + "generate")
        response.raise_for_status()
        payload = response.json()
    if payload.get("code") != 0 or not (payload.get("data") or {}).get("qrcode_key"):
        raise ValueError("B站暂不接受扫码登录，请稍后重试或改用粘贴 Cookie")
    data = payload["data"]
    buf = io.BytesIO()
    qrcode.make(data["url"]).save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "qrcode_key": str(data["qrcode_key"]),
        "qr_image": f"data:image/png;base64,{encoded}",
    }


async def poll_qr_login(session, qrcode_key: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", qrcode_key or ""):
        raise ValueError("无效的二维码会话标识")
    async with httpx.AsyncClient(
        timeout=15,
        trust_env=False,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
    ) as client:
        response = await client.get(PASSPORT_QR_BASE + "poll", params={"qrcode_key": qrcode_key})
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError("B站拒绝登录状态查询，请重新生成二维码")
        code = (payload.get("data") or {}).get("code")
        if code == 0:
            return await save_login_cookies(session, login_response_cookies(client), "扫码登录成功，凭据已保存")
    if code == 86090:
        return {"state": "scanned", "message": "已扫码，请在 B 站手机客户端确认登录"}
    if code == 86101:
        return {"state": "waiting", "message": "请使用 B 站手机客户端扫码"}
    if code == 86038:
        return {"state": "expired", "message": "二维码已过期，请重新生成"}
    raise ValueError("B站要求额外安全验证，请在网页登录后粘贴 Cookie")


async def save_manual_cookie(session, cookie_text: str) -> dict:
    cookies = parse_cookie_text(cookie_text)
    await set_setting(
        session,
        CREDENTIAL_SETTING_KEY,
        {"cookies": cookies, "updated_at": utcnow().isoformat()},
    )
    return await check_status(session)


async def clear_credentials(session) -> dict:
    await set_setting(session, CREDENTIAL_SETTING_KEY, {})
    await set_setting(session, SETTING_KEY, {})
    return await get_status(session)
