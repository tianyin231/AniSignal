"""将 RSS 2.0、Atom 和 RDF 信源接入统一采集队列。"""

import asyncio
import hashlib
import ipaddress
import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import bleach
import httpx

from app.collectors.base import CollectorError, USER_AGENT
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs

MAX_FEED_BYTES = 5 * 1024 * 1024


def rss_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:48]


def tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def child(element, *names):
    return next((e for e in element if tag(e) in names), None)


def text(element, *names) -> str:
    value = child(element, *names)
    return "".join(value.itertext()).strip() if value is not None else ""


# 微软研究院的 RSS 全文里嵌着站内推广块，每次抓取随机换一段，同一篇文章因此不断变成新版本。
_ROTATING_BLOCK = re.compile(r'<div\b[^>]*\bmsr-promo\b[^>]*>', re.IGNORECASE)
_DIV_TAG = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)


def strip_rotating_blocks(html: str) -> str:
    """去掉随机轮换的推广块；块没有闭合时原样保留，宁可多留也不吞正文。"""
    while (block := _ROTATING_BLOCK.search(html)) is not None:
        depth = 0
        for tag in _DIV_TAG.finditer(html, block.start()):
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                html = html[:block.start()] + html[tag.end():]
                break
        else:
            return html
    return html


def plain(value: str) -> str:
    from html import unescape
    return unescape(bleach.clean(value, tags=[], strip=True)).strip()


def feed_time(value: str):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)


def _is_permitted_egress_ip(raw_ip: str, allow_networks: tuple[ipaddress._BaseNetwork, ...]) -> bool:
    ip = ipaddress.ip_address(raw_ip)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_global or any(ip in net for net in allow_networks)


async def public_feed_url(value: str) -> str:
    """每次重定向都检查；采集地址不能访问本机、云元数据或私有网络。"""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or "\\" in value:
        raise CollectorError("RSS 地址必须是公开 HTTP(S) 地址")
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CollectorError("RSS 地址无法解析") from exc
    if not addresses:
        raise CollectorError("RSS 地址无法解析")
    allow_networks = get_settings().egress_allow_networks
    for row in addresses:
        addr = row[4][0]
        if not _is_permitted_egress_ip(addr, allow_networks):
            raise CollectorError(
                f"RSS 地址不能指向私有网络（解析到 {addr}；若使用 fake-ip 代理可设置 ANISIGNAL_EGRESS_ALLOW_CIDRS=198.18.0.0/15）"
            )
    return value


def parse_feed(body: bytes, feed_url: str, *, config=None, limit=20) -> list[dict]:
    config = config or {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CollectorError("信源返回的内容不是有效 RSS/Atom XML") from exc
    if tag(root) not in {"rss", "feed", "rdf"}:
        raise CollectorError("信源不是 RSS/Atom/RDF")
    container = child(root, "channel") if tag(root) == "rss" else root
    if container is None:
        raise CollectorError("RSS 缺少 channel")
    source_name = config.get("name") or text(container, "title") or urlsplit(feed_url).hostname
    rows, seen = [], set()
    for entry in container:
        if tag(entry) not in {"item", "entry"}:
            continue
        links = [e for e in entry if tag(e) == "link"]
        href = next((e.get("href") for e in links if e.get("rel", "alternate") == "alternate" and e.get("href")), "")
        href = href or text(entry, "link")
        if not href:
            continue
        url = urljoin(entry.get("{http://www.w3.org/XML/1998/namespace}base", feed_url), href)
        if urlsplit(url).scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        html = text(entry, "encoded") or text(entry, "content") or text(entry, "description", "summary")
        author = child(entry, "author")
        author_name = text(author, "name") if author is not None and len(author) else text(entry, "author", "creator")
        date = feed_time(text(entry, "pubdate", "published", "date") or text(entry, "updated"))
        media = []
        for e in entry.iter():
            if tag(e) in {"thumbnail", "content", "enclosure"}:
                candidate = e.get("url")
                mime = e.get("type", "")
                if candidate and (tag(e) == "thumbnail" or mime.startswith("image/") or e.get("medium") == "image"):
                    media.append(urljoin(url, candidate))
        rows.append({"id": rss_id(url), "url": url, "title": plain(text(entry, "title")),
            "text": plain(strip_rotating_blocks(html)), "html": html, "published": date.isoformat() if date else None,
            "feed_url": config.get("feed_url") or feed_url, "source_name": source_name, "author": plain(author_name),
            "first_party": bool(config.get("first_party")), "owner_key": config.get("owner_key"),
            "participation_mode": config.get("participation_mode", "editorial"), "tier": config.get("tier", "T2"),
            "media": list(dict.fromkeys(media))[:12]})
    rows.sort(key=lambda row: row["published"] or "", reverse=True)
    return rows[:limit]


class RssCollector:
    platform = Platform.RSS

    async def fetch(self, target_type, target_value, limit):
        if target_type != "feed":
            raise CollectorError("RSS 采集只支持 feed 类型")
        url = target_value
        async with httpx.AsyncClient(timeout=get_settings().http_timeout_seconds,
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, */*;q=0.5"},
                follow_redirects=False, **http_proxy_kwargs(self)) as client:
            for _ in range(6):
                await public_feed_url(url)
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_FEED_BYTES:
                            raise CollectorError("RSS 内容超过 5 MB")
                return FetchResult(platform=self.platform, fetch_kind="rss_feed",
                    items=parse_feed(bytes(body), url, config=getattr(self, "source_config", {}), limit=limit))
        raise CollectorError("RSS 重定向次数过多")
