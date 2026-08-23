"""YouTube 官方频道 RSS 采集器（官方公开 feed，无需 API Key）。

RSS 是原始层：collector 把 XML entry 整理成 dict 存 raw_json（不下载视频，
只保存视频 ID、标题、发布时间、缩略图与原链，见 data-sources.md）。
"""

import xml.etree.ElementTree as ET

import httpx

from app.collectors.base import USER_AGENT, CollectorError
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult

FEED_URL = "https://www.youtube.com/feeds/videos.xml"

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

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "channel_rss":
            raise CollectorError(f"unsupported youtube target_type: {target_type}")
        channel_id = target_value.strip()
        if not channel_id:
            raise CollectorError("youtube channel_rss requires channel id")
        items = await self.fetch_feed(channel_id, limit)
        return FetchResult(platform=self.platform, fetch_kind="youtube_rss", items=items)

    async def fetch_feed(self, channel_id: str, limit: int) -> list[dict]:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(
                FEED_URL, params={"channel_id": channel_id}, timeout=self._timeout
            )
            resp.raise_for_status()
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
