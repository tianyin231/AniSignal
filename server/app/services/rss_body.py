"""短摘要 RSS 补读公开正文；复用已保存正文，避免每轮重复下载或退回摘要。"""

import asyncio
import logging
import re
from urllib.parse import urljoin

import httpx
from sqlalchemy import func, select
from trafilatura import extract

from app.collectors.base import CollectorError, USER_AGENT
from app.collectors.rss import MAX_FEED_BYTES, public_feed_url, rss_id
from app.models import ContentVersion, RawContent
from app.services.collector_proxy import http_proxy_kwargs

logger = logging.getLogger(__name__)
TEASER = re.compile(r"read (?:the )?full (?:story|article)|continue reading|read more|appeared first on|…\s*$|\[\s*\.\.\.\s*\]", re.I)


def needs_body(text):
    return len(text.strip()) < 280 or len(text) < 1200 and bool(TEASER.search(text))


async def fetch_body(client, url):
    for _ in range(6):
        await public_feed_url(url)
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "").lower():
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_FEED_BYTES:
                    raise CollectorError("资讯正文超过 5 MB")
        return await asyncio.to_thread(extract, bytes(body), url=url, include_comments=False,
            include_tables=False, fast=True, favor_precision=True)
    return None


async def enrich_items(session, collector, items):
    if not items or getattr(collector, "source_config", {}).get("participation_mode", "editorial") != "editorial":
        return
    ids = [item["id"] for item in items]
    latest = select(func.max(ContentVersion.version)).where(ContentVersion.raw_content_id == RawContent.id).correlate(RawContent).scalar_subquery()
    cached = {row[0]: row[1:] for row in await session.execute(select(RawContent.external_id,
        ContentVersion.title, ContentVersion.text, ContentVersion.normalized_json).join(ContentVersion,
        (ContentVersion.raw_content_id == RawContent.id) & (ContentVersion.version == latest))
        .where(RawContent.platform == "rss", RawContent.external_id.in_(ids)))}
    pending = []
    for item in items:
        feed_text = item.get("text") or ""
        signature = rss_id((item.get("title") or "") + "\n" + feed_text)
        prior = cached.get(item["id"])
        extra = ((prior[2] or {}).get("extra") or {}) if prior else {}
        reusable = prior and (extra.get("rss_feed_hash") == signature or
            not extra.get("rss_feed_hash") and prior[0] == item.get("title")
            and needs_body(feed_text) and len(prior[1] or "") > len(feed_text))
        item["feed_text"], item["rss_feed_hash"] = feed_text, signature
        if reusable:
            item["text"] = prior[1]
            item["body_extracted"] = bool(extra.get("body_extracted") or needs_body(feed_text))
        elif needs_body(feed_text):
            pending.append(item)
    semaphore = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=False,
            **http_proxy_kwargs(collector)) as client:
        async def enrich(item):
            async with semaphore:
                try:
                    body = await fetch_body(client, item["url"])
                    if body and len(body) > len(item["text"]):
                        item["text"], item["body_extracted"] = body, True
                except (httpx.HTTPError, CollectorError, ValueError):
                    # 可选补文失败仍保留 feed 原文；下一轮可重试，不把片段标作全文。
                    logger.debug("RSS optional body unavailable", exc_info=True)
        await asyncio.gather(*(enrich(item) for item in pending))
