"""B站授权会话：CLI 与 HTTP 共用文件，仅保存脱敏的健康状态。"""

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import unquote, quote

import httpx

from app.clock import utcnow
from app.config import get_settings
from app.services.settings import get_setting, set_setting

logger = logging.getLogger(__name__)
SETTING_KEY = "bilibili_session_status"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


def read_cookie_file(path: str) -> dict[str, str]:
    """与 bili 的纯请求头/Netscape 文件格式一致；不从其他进程环境隐式取凭据。"""
    if not path:
        return {}
    content = Path(path).read_text(encoding="utf-8-sig")
    parts = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        parts.append(f"{fields[5]}={fields[6]}" if len(fields) == 7 else line)
    cookies = {}
    for part in "; ".join(parts).split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and re.fullmatch(r"[A-Za-z0-9_]+", name) and value:
            cookies[name] = value
    if not cookies.get("SESSDATA"):
        raise ValueError("B站凭据文件缺少 SESSDATA，请重新登录")
    return cookies


def cookie_header(cookies: dict) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def credential_version(cookies: dict) -> str:
    return hashlib.sha256(cookie_header(cookies).encode()).hexdigest()[:12] if cookies else ""


def redact(text: str, cookies: dict) -> str:
    # 先替换完整请求头，再覆盖原值/URL 编解码值；CLI 错误也可能回显请求信息。
    if cookies:
        text = text.replace(cookie_header(cookies), "[B站凭据已隐藏]")
    secrets = {variant for value in cookies.values() for variant in (value, unquote(value), quote(value, safe="")) if value}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return re.sub(r"(?i)\b(SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|sid|buvid[34])\s*=\s*[^;\s\"']+", r"\1=***", text)


def local_status() -> tuple[dict, dict]:
    path = get_settings().bili_cookie_file
    state = {"state": "unconfigured", "message": "未配置B站登录态，当前匿名采集", "credential_version": ""}
    if not path:
        return state, {}
    try:
        cookies = read_cookie_file(path)
    except FileNotFoundError:
        return {**state, "state": "missing", "message": "尚未登录B站，请完成扫码授权"}, {}
    except (OSError, ValueError, UnicodeError):
        return {**state, "state": "invalid", "message": "B站凭据文件不可用，请重新登录"}, {}
    return {**state, "state": "unchecked", "message": "B站凭据已就绪，等待检测", "credential_version": credential_version(cookies)}, cookies


async def get_status(session) -> dict:
    state, _ = local_status()
    stored = await get_setting(session, SETTING_KEY) or {}
    if state["state"] != "unchecked" or stored.get("credential_version") != state["credential_version"]:
        return state
    return stored or state


async def check_status(session) -> dict:
    state, cookies = local_status()
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
