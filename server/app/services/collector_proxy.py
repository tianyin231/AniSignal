"""采集代理池：配置存 KV，每次采集选一个出口，任务内保持不变。"""

import itertools
import copy
import re
import uuid
from urllib.parse import quote, unquote, urlsplit

from app.collectors.base import CollectorError
from app.models.enums import Platform
from app.services.settings import get_setting

SETTING_KEY = "collector_proxies"
PLATFORMS = [p.value for p in Platform] + ["aihot"]
SCHEMES = {"http", "https", "socks5"}
_cursor = itertools.count()


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
    return {"enabled": bool(stored.get("enabled")), "entries": stored.get("entries") or [],
            "platforms": {p: bool(stored.get("platforms", {}).get(p, True)) for p in PLATFORMS}}


def public_config(config: dict) -> dict:
    return {**config, "entries": [public_entry(entry) for entry in config["entries"]]}


async def choose_proxy(session, platform: str, redis=None) -> str | None:
    config = await get_config(session)
    if not config["enabled"] or not config["platforms"].get(str(platform), True):
        return None
    entries = [entry for entry in config["entries"] if entry.get("enabled")]
    if not entries:
        raise CollectorError("代理池已启用，但没有可用的代理，请在管理端添加或启用代理")
    index = next(_cursor)
    if redis is not None and hasattr(redis, "incr"):
        index = int(await redis.incr("anisignal:collector-proxy:cursor")) - 1
    return proxy_url(entries[index % len(entries)])


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
