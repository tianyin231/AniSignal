"""自定义网页监控采集器：支持单页变更监控（page_watch）与列表页新增发现（list_watch）。

安全与资源边界：
- 每跳重定向（≤ 6 跳）执行 DNS/IP 校验，禁止访问内网、回环与云元数据地址；
- 单页上限 5 MB，拒绝二进制文件；
- 支持 ETag / Last-Modified 条件请求，304 Not Modified 直接短路零解析开销。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from urllib.parse import urljoin, urlsplit

import httpx

from app.collectors.base import CollectorError, USER_AGENT
from app.collectors.rss import MAX_FEED_BYTES, public_feed_url
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.schemas.ids import web_id
from app.services.collector_proxy import http_proxy_kwargs
from app.services.web_parser import (
    DetectedFeed,
    WebParseError,
    extract_list,
    extract_page,
    normalize_web_config,
)

logger = logging.getLogger(__name__)

MAX_PAGE_BYTES = MAX_FEED_BYTES
MAX_REDIRECTS = 6
MAX_LIST_ENRICH = 8
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "",
)
WEB_ACCEPT = "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.5"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status_code: int
    body: bytes
    content_type: str
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


def web_rule_signature(config: dict | None) -> str:
    """对解析配置生成短签名，用于区分同一 URL 下的不同选择器规则及失效 304 缓存。"""
    cfg = normalize_web_config(config or {})
    if not cfg:
        return "default"
    parts = [
        cfg.get("selector", ""),
        cfg.get("exclude_selector", ""),
        cfg.get("link_pattern", ""),
        "\n".join(cfg.get("ignore_patterns") or []),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


async def public_web_url(value: str) -> str:
    """复用全站 SSRF 校验（含 DNS 解析与 fake-ip 白名单），输出网页场景提示文案。"""
    try:
        return await public_feed_url(value)
    except CollectorError as exc:
        msg = str(exc).replace("RSS 地址", "网页地址")
        err = CollectorError(msg)
        # SSRF / 非法地址不应继续切换代理出口重试
        err.stop_fallback = True
        raise err from exc


def _check_content_type(raw_ct: str) -> str:
    ct = (raw_ct or "").split(";", 1)[0].strip().lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        err = CollectorError(f"目标地址不是可监控的网页（Content-Type: {ct}）")
        err.stop_fallback = True
        raise err
    return raw_ct


async def fetch_webpage_bytes(
    url: str,
    *,
    proxy_url: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    timeout: float | None = None,
) -> FetchedPage:
    """带逐跳 SSRF 校验、体积上限与条件请求头的公开网页抓取。"""
    headers = {"User-Agent": USER_AGENT, "Accept": WEB_ACCEPT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    current = url
    eff_timeout = timeout or get_settings().http_timeout_seconds
    async with httpx.AsyncClient(
        timeout=eff_timeout,
        headers=headers,
        follow_redirects=False,
        proxy=proxy_url,
        trust_env=False,
    ) as client:
        for _ in range(MAX_REDIRECTS):
            await public_web_url(current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise CollectorError("网页重定向缺少 Location")
                    current = urljoin(current, location)
                    continue
                if response.status_code == 304:
                    return FetchedPage(
                        url=current,
                        status_code=304,
                        body=b"",
                        content_type="",
                        etag=response.headers.get("etag") or etag,
                        last_modified=response.headers.get("last-modified") or last_modified,
                        not_modified=True,
                    )
                response.raise_for_status()
                raw_ct = _check_content_type(response.headers.get("content-type", ""))
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_PAGE_BYTES:
                        raise CollectorError("网页内容超过 5 MB 限制")
                return FetchedPage(
                    url=current,
                    status_code=response.status_code,
                    body=bytes(body),
                    content_type=raw_ct,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    not_modified=False,
                )
    raise CollectorError("网页重定向次数过多")


def _serialize_feeds(feeds: list[DetectedFeed]) -> list[dict]:
    return [{"url": f.url, "title": f.title, "type": f.feed_type} for f in feeds]


def build_page_watch_item(
    fetched: FetchedPage,
    *,
    source_url: str,
    web_cfg: dict,
    source_name: str | None = None,
    is_official: bool = False,
) -> dict:
    """在内存里将已抓 HTML 解析为单页监控 raw item。"""
    parsed = extract_page(
        fetched.body,
        fetched.url,
        content_type=fetched.content_type,
        selector=web_cfg.get("selector", ""),
        exclude_selector=web_cfg.get("exclude_selector", ""),
        ignore_patterns=web_cfg.get("ignore_patterns") or (),
    )
    sig = web_rule_signature(web_cfg)
    item_id = web_id(f"page:{source_url}:{sig}")
    source_id = web_id(f"site:{source_url}")
    text_hash = hashlib.sha256(f"{parsed.title}\n{parsed.text}".encode("utf-8")).hexdigest()[:32]
    return {
        "id": item_id,
        "source_id": source_id,
        "watch_mode": "page_watch",
        "url": fetched.url,
        "source_url": source_url,
        "title": parsed.title,
        "text": parsed.text,
        "published": parsed.published_at.isoformat() if parsed.published_at else None,
        "source_name": source_name or parsed.site_name or urlsplit(source_url).hostname or source_url,
        "author": parsed.author,
        "media": parsed.media,
        "extractor": parsed.extractor,
        "selector": web_cfg.get("selector", ""),
        "rule_sig": sig,
        "text_hash": text_hash,
        "spa_warning": parsed.spa_warning,
        "detected_feeds": _serialize_feeds(parsed.detected_feeds),
        "is_official": bool(is_official),
    }


def build_list_watch_items(
    fetched: FetchedPage,
    *,
    source_url: str,
    web_cfg: dict,
    limit: int = 20,
    source_name: str | None = None,
    is_official: bool = False,
) -> tuple[list[dict], dict]:
    """在内存里将列表页 HTML 解析为多条候选文章 raw items。"""
    links, meta = extract_list(
        fetched.body,
        fetched.url,
        content_type=fetched.content_type,
        selector=web_cfg.get("selector", ""),
        exclude_selector=web_cfg.get("exclude_selector", ""),
        link_pattern=web_cfg.get("link_pattern", ""),
        limit=limit,
    )
    source_id = web_id(f"site:{source_url}")
    resolved_source_name = source_name or meta.get("site_name") or urlsplit(source_url).hostname or source_url
    sig = web_rule_signature(web_cfg)
    items: list[dict] = []
    for link in links:
        items.append({
            "id": web_id(f"list:{link.url}"),
            "source_id": source_id,
            "watch_mode": "list_watch",
            "url": link.url,
            "source_url": source_url,
            "title": link.title,
            "text": link.excerpt,
            "published": link.published_at.isoformat() if link.published_at else None,
            "source_name": resolved_source_name,
            "author": None,
            "media": [link.cover] if link.cover else [],
            "extractor": "list",
            "selector": web_cfg.get("selector", ""),
            "rule_sig": sig,
            "is_official": bool(is_official),
            "body_extracted": False,
        })
    return items, meta


async def enrich_list_items(
    items: list[dict],
    *,
    existing_ids: set[str] | None = None,
    proxy_url: str | None = None,
    max_fetch: int = MAX_LIST_ENRICH,
) -> None:
    """只对新发现的列表文章并发补读正文与精确日期，已有条目跳过以节约带宽。"""
    skip = existing_ids or set()
    pending = [item for item in items if item.get("id") not in skip and item.get("url")][:max_fetch]
    if not pending:
        return
    semaphore = asyncio.Semaphore(2)

    async def enrich_one(item: dict) -> None:
        async with semaphore:
            try:
                page = await fetch_webpage_bytes(item["url"], proxy_url=proxy_url, timeout=15)
                if page.not_modified or not page.body:
                    return
                extracted = await asyncio.to_thread(
                    extract_page,
                    page.body,
                    page.url,
                    content_type=page.content_type,
                )
                if extracted.text:
                    item["text"] = extracted.text
                    item["body_extracted"] = True
                if extracted.title and len(extracted.title) > len(item.get("title") or ""):
                    item["title"] = extracted.title
                if not item.get("published") and extracted.published_at:
                    item["published"] = extracted.published_at.isoformat()
                if extracted.author:
                    item["author"] = extracted.author
                if extracted.media and not item.get("media"):
                    item["media"] = extracted.media
            except (httpx.HTTPError, CollectorError, WebParseError, ValueError):
                logger.debug("web_page list item body enrich failed: %s", item.get("url"), exc_info=True)

    await asyncio.gather(*(enrich_one(item) for item in pending))


async def preview_webpage(
    url: str,
    *,
    mode: str = "page_watch",
    web_config: dict | None = None,
    proxy_url: str | None = None,
) -> dict:
    """供管理端/用户端「测试解析」使用：抓一次并立即返回提取预览、RSS 探测与 SPA 提示。"""
    if mode not in {"page_watch", "list_watch"}:
        raise CollectorError("仅支持 page_watch（单页变更）或 list_watch（列表新增）")
    cfg = normalize_web_config(web_config)
    fetched = await fetch_webpage_bytes(url, proxy_url=proxy_url, timeout=15)
    if mode == "page_watch":
        page = await asyncio.to_thread(
            extract_page,
            fetched.body,
            fetched.url,
            content_type=fetched.content_type,
            selector=cfg.get("selector", ""),
            exclude_selector=cfg.get("exclude_selector", ""),
            ignore_patterns=cfg.get("ignore_patterns") or (),
        )
        return {
            "mode": "page_watch",
            "url": fetched.url,
            "title": page.title,
            "site_name": page.site_name,
            "published_at": page.published_at.isoformat() if page.published_at else None,
            "extractor": page.extractor,
            "text_length": len(page.text),
            "text_preview": page.text[:800],
            "media_count": len(page.media),
            "detected_feeds": _serialize_feeds(page.detected_feeds),
            "spa_warning": page.spa_warning,
            "items": [],
        }

    links, meta = await asyncio.to_thread(
        extract_list,
        fetched.body,
        fetched.url,
        content_type=fetched.content_type,
        selector=cfg.get("selector", ""),
        exclude_selector=cfg.get("exclude_selector", ""),
        link_pattern=cfg.get("link_pattern", ""),
        limit=12,
    )
    return {
        "mode": "list_watch",
        "url": fetched.url,
        "title": meta.get("title") or url,
        "site_name": meta.get("site_name"),
        "published_at": None,
        "extractor": "selector" if cfg.get("selector") else "list",
        "text_length": 0,
        "text_preview": "",
        "media_count": 0,
        "detected_feeds": _serialize_feeds(meta.get("detected_feeds") or []),
        "spa_warning": bool(meta.get("spa_warning")),
        "items": [
            {
                "title": link.title,
                "url": link.url,
                "published_at": link.published_at.isoformat() if link.published_at else None,
            }
            for link in links
        ],
    }


class WebPageCollector:
    platform = Platform.WEB_PAGE

    def __init__(self) -> None:
        self.proxy_url: str | None = None
        self.source_config: dict = {}
        self.request_count: int = 0
        self.not_modified: bool = False
        self.last_http_cache: dict | None = None

    async def fetch(self, target_type: str, target_value: str, limit: int = 20) -> FetchResult:
        if target_type not in {"page_watch", "list_watch"}:
            raise CollectorError(f"网页监控不支持采集类型：{target_type}")
        cfg = self.source_config or {}
        web_cfg = normalize_web_config(cfg.get("web") if isinstance(cfg.get("web"), dict) else None)
        sig = web_rule_signature(web_cfg)

        http_cache = cfg.get("_http_cache") if isinstance(cfg.get("_http_cache"), dict) else {}
        use_cache = http_cache.get("sig") == sig and http_cache.get("url") == target_value
        etag = http_cache.get("etag") if use_cache else None
        last_modified = http_cache.get("last_modified") if use_cache else None

        self.request_count += 1
        fetched = await fetch_webpage_bytes(
            target_value,
            proxy_url=self.proxy_url,
            etag=etag,
            last_modified=last_modified,
        )
        self.not_modified = fetched.not_modified
        if fetched.etag or fetched.last_modified:
            self.last_http_cache = {
                "url": target_value,
                "sig": sig,
                "etag": fetched.etag,
                "last_modified": fetched.last_modified,
            }
        fetch_kind = "web_page_watch" if target_type == "page_watch" else "web_page_list"
        if fetched.not_modified:
            return FetchResult(platform=self.platform, fetch_kind=fetch_kind, items=[])

        try:
            if target_type == "page_watch":
                item = await asyncio.to_thread(
                    build_page_watch_item,
                    fetched,
                    source_url=target_value,
                    web_cfg=web_cfg,
                    source_name=cfg.get("name"),
                    is_official=bool(cfg.get("is_official")),
                )
                return FetchResult(platform=self.platform, fetch_kind=fetch_kind, items=[item])

            items, _meta = await asyncio.to_thread(
                build_list_watch_items,
                fetched,
                source_url=target_value,
                web_cfg=web_cfg,
                limit=limit,
                source_name=cfg.get("name"),
                is_official=bool(cfg.get("is_official")),
            )
            return FetchResult(platform=self.platform, fetch_kind=fetch_kind, items=items)
        except WebParseError as exc:
            err = CollectorError(str(exc))
            err.stop_fallback = True
            raise err from exc
