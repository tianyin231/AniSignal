"""YouTube 官方频道 RSS 采集器（官方公开 feed，无需 API Key）。

RSS 是原始层：collector 把 XML entry 整理成 dict 存 raw_json（不下载视频，
只保存视频 ID、标题、发布时间、缩略图与原链，见 data-sources.md）。
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from app.collectors.base import USER_AGENT, CollectorError
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
# 「@handle」或 youtube.com/@handle 链接；handle 规则：3–30 位字母数字、点、下划线、连字符。
_HANDLE_RE = re.compile(r"^(?:(?:https?://)?(?:www\.|m\.)?youtube\.com/)?(@[A-Za-z0-9._-]{3,30})(?:[/?#].*)?$")
# 频道页的 canonical 链接才是本频道；页面里其他 "channelId" 可能是推荐频道。
_CANONICAL_RE = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"')
_EXTERNAL_ID_RE = re.compile(r'"externalId":"(UC[A-Za-z0-9_-]{22})"')
HANDLE_HELP = "无法解析该 YouTube @handle，请改填 UC 开头的频道 ID"


def youtube_handle(value: str) -> str | None:
    """从用户输入里取出 @handle；不是 handle 形式时返回 None。"""
    m = _HANDLE_RE.match((value or "").strip())
    return m.group(1) if m else None


async def resolve_youtube_handle(session, handle: str) -> str:
    """创建/修改目标时请求一次频道页，把 @handle 换成 UC 频道 ID（无需 API Key，沿用采集出口）。"""
    from app.services.collector_proxy import fetch_with_fallback

    collector = YoutubeRssCollector()
    try:
        return await fetch_with_fallback(collector, session, "youtube", lambda: collector.resolve_handle(handle))
    except (CollectorError, httpx.HTTPError, TimeoutError) as exc:
        raise CollectorError(HANDLE_HELP) from exc

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


class YoutubeRssCollector:
    platform = Platform.YOUTUBE

    def __init__(self) -> None:
        settings = get_settings()
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_max_retries

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "channel_rss":
            raise CollectorError(f"unsupported youtube target_type: {target_type}")
        channel_id = target_value.strip()
        if not channel_id:
            raise CollectorError("youtube channel_rss requires channel id")
        items = await self.fetch_feed(channel_id, limit)
        return FetchResult(platform=self.platform, fetch_kind="youtube_rss", items=items)

    async def resolve_handle(self, handle: str) -> str:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            cookies={"SOCS": "CAI"},  # 跳过欧盟地区的 Cookie 同意页
            follow_redirects=True,
            **http_proxy_kwargs(self),
        ) as client:
            resp = await client.get(f"https://www.youtube.com/{quote(handle, safe='@')}", timeout=self._timeout)
            resp.raise_for_status()
        m = _CANONICAL_RE.search(resp.text) or _EXTERNAL_ID_RE.search(resp.text)
        if not m:
            raise CollectorError(HANDLE_HELP)
        return m.group(1)

    async def fetch_feed(self, channel_id: str, limit: int) -> list[dict]:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, **http_proxy_kwargs(self)) as client:
            for attempt in range(self._max_retries + 1):
                if attempt:
                    await asyncio.sleep(min(2**attempt, 8))
                try:
                    resp = await client.get(
                        FEED_URL, params={"channel_id": channel_id}, timeout=self._timeout
                    )
                    resp.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    retryable = status in (404, 429) or status >= 500
                    if not retryable or attempt == self._max_retries:
                        raise
                except httpx.TransportError:
                    if attempt == self._max_retries:
                        raise
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise CollectorError(f"cannot parse youtube rss: {exc}") from exc

        items: list[dict] = []
        for entry in root.findall("atom:entry", _NS)[:limit]:
            video_id = (entry.findtext("yt:videoId", default="", namespaces=_NS) or "").strip()
            if not video_id:
                continue
            link = entry.find("atom:link", _NS)
            thumbnail = entry.find("media:group/media:thumbnail", _NS)
            items.append(
                {
                    "id": video_id,
                    "channel_id": channel_id,
                    "title": entry.findtext("atom:title", default="", namespaces=_NS),
                    "link": (link.get("href") if link is not None else None)
                    or f"https://www.youtube.com/watch?v={video_id}",
                    "published": entry.findtext("atom:published", default="", namespaces=_NS),
                    "updated": entry.findtext("atom:updated", default="", namespaces=_NS),
                    "author": entry.findtext("atom:author/atom:name", default="", namespaces=_NS),
                    "thumbnail": thumbnail.get("url") if thumbnail is not None else "",
                    "summary": entry.findtext(
                        "media:group/media:description", default="", namespaces=_NS
                    ),
                }
            )
        return items
