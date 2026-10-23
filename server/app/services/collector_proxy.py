"""采集出口按管理端顺序尝试；默认直连，成功即停止，不因没有新内容换出口。"""

import copy
import logging
import re
import uuid
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.collectors.base import CollectorDeferred, CollectorError
from app.models.enums import Platform
from app.services.settings import get_setting

SETTING_KEY = "collector_proxies"
PLATFORMS = [p.value for p in Platform] + ["aihot"]
SCHEMES = {"http", "https", "socks5"}
logger = logging.getLogger(__name__)


def parse_proxy(line: str, default_scheme: str = "http") -> dict:
    """支持 URL、host:port:user:pass、user:pass@host:port、四列文本。"""
    value = line.lstrip("\ufeff").strip()
    if not value or any(ord(c) < 32 for c in value.replace("\t", "")):
        raise ValueError("代理地址为空或包含控制字符")
    if default_scheme not in SCHEMES:
        raise ValueError("协议只支持 HTTP、HTTPS、SOCKS5")
    if "://" not in value:
        fields = re.fullmatch(r"(\[[^\]]+\]|[^:\s]+):(\d+)(?::([^:]*):(.+))?", value)
        if not fields and "@" not in value:
            columns = re.split(r"[\t, ]+", value, maxsplit=3)
            if len(columns) == 4 and columns[1].isdigit():
                host, port, user, password = columns
                value = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        elif fields:
            host, port, user, password = fields.groups()
            auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user is not None else ""
            value = f"{auth}{host}:{port}"
        value = f"{default_scheme}://{value}"
    try:
        parsed = urlsplit(value)
        scheme = "socks5" if parsed.scheme.lower() == "socks5h" else parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port if parsed.port is not None else {"http": 80, "https": 443, "socks5": 1080}.get(scheme, 0)
        if scheme not in SCHEMES or not host or any(c.isspace() for c in host):
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or not 1 <= port <= 65535:
            raise ValueError
        user = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if any(ord(c) < 32 for c in host + user + password):
            raise ValueError
    except ValueError:
        raise ValueError("代理格式错误，请检查协议、地址、端口及认证信息") from None
    return {"id": uuid.uuid4().hex[:16], "label": "", "scheme": scheme, "host": host,
            "port": port, "username": user, "password": password, "enabled": True}


def proxy_url(entry: dict) -> str:
    host = entry["host"]
    if ":" in host:
        host = f"[{host}]"
    user, password = entry.get("username", ""), entry.get("password", "")
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user or password else ""
    return f"{entry['scheme']}://{auth}{host}:{entry['port']}"


def public_entry(entry: dict) -> dict:
    return {**{k: entry.get(k) for k in ("id", "label", "scheme", "host", "port", "username", "enabled", "last_test")},
            "password_set": bool(entry.get("password"))}


async def get_config(session) -> dict:
    # 不原地修改 ORM 原值的嵌套列表，否则 JSON 变更可能不会持久化。
    stored = copy.deepcopy(await get_setting(session, SETTING_KEY) or {})
    entries = stored.get("entries") or []
    ids = ["direct", *(entry["id"] for entry in entries)]
    order = list(dict.fromkeys(item for item in stored.get("order", []) if item in ids))
    order.extend(item for item in ids if item not in order)
    return {"enabled": bool(stored.get("enabled")), "entries": entries, "order": order,
            "platforms": {p: bool(stored.get("platforms", {}).get(p, True)) for p in PLATFORMS}}


def public_config(config: dict) -> dict:
    return {**config, "entries": [public_entry(entry) for entry in config["entries"]]}


async def collection_routes(session, platform: str) -> list[str | None]:
    if session is None:
        return [None]
    config = await get_config(session)
    if not config["enabled"] or not config["platforms"].get(str(platform), True):
        return [None]
    entries = {entry["id"]: entry for entry in config["entries"] if entry.get("enabled")}
    return [None if key == "direct" else proxy_url(entries[key])
            for key in config["order"] if key == "direct" or key in entries]


async def fetch_with_fallback(collector, session, platform: str, fetch):
    """只重试读取，不重复入库或后处理。每个出口至多尝试一轮。"""
    last_error = None
    routes = await collection_routes(session, platform)
    for index, url in enumerate(routes):
        configure_collector(collector, url)
        try:
            return await fetch()
        except CollectorDeferred as exc:
            # 冷却中的出口不发请求；保留真实失败，避免它被后续冷却覆盖。
            last_error = last_error or exc
        except (CollectorError, httpx.HTTPError, TimeoutError) as exc:
            exc.args = (redact_proxy(str(exc), url),)
            last_error = exc
            logger.warning("%s collection exit %d/%d failed: %s", platform, index + 1, len(routes), type(exc).__name__)
    raise last_error


def configure_collector(collector, url: str | None) -> None:
    collector.proxy_url = url
    # YouTube API 回退 RSS 时沿用本轮出口。
    if getattr(collector, "_rss", None) is not None:
        collector._rss.proxy_url = url


def http_proxy_kwargs(collector) -> dict:
    return {"proxy": getattr(collector, "proxy_url", None), "trust_env": False}


def redact_proxy(text: str, url: str | None) -> str:
    if not url:
        return text
    parsed = urlsplit(url)
    text = text.replace(url, f"{parsed.scheme}://***@{parsed.hostname}:{parsed.port}")
    if parsed.password:
        for secret in {parsed.password, unquote(parsed.password)}:
            text = text.replace(secret, "***")
    return text
