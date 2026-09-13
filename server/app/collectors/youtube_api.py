"""YouTube 官方 Data API v3 采集器（配额内稳定，规避匿名 RSS 的 IP 风控）。

接口与产出完全对齐 YoutubeRssCollector：同样的 target_type（channel_rss）、
同样的 dict 结构（id/channel_id/title/link/published/updated/author/thumbnail/summary），
fetch_kind 仍为 "youtube_rss"，解析器无需改动。

配额：playlistItems.list 每频道每轮 1 单位（默认日配额 10000）。
API 失败（配额耗尽/key 失效/网络）自动回落到 RSS 采集器，数据流不断。
"""

import logging

import httpx

from app.collectors.base import USER_AGENT, CollectorError
from app.collectors.youtube_rss import YoutubeRssCollector
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult

API_BASE = "https://www.googleapis.com/youtube/v3"

logger = logging.getLogger(__name__)


def uploads_playlist_id(channel_id: str) -> str:
    """频道上传播放列表 = UU + 频道 ID 去掉 UC 前缀。"""
    if not channel_id.startswith("UC") or len(channel_id) < 3:
        raise CollectorError(f"invalid youtube channel id: {channel_id}")
    return "UU" + channel_id[2:]


def _best_thumbnail(thumbnails: dict) -> str:
    for size in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbnails.get(size)
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    return ""


class YoutubeApiCollector:
    platform = Platform.YOUTUBE

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.youtube_api_key.strip()
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_max_retries
        self._rss = YoutubeRssCollector()

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "channel_rss":
            raise CollectorError(f"unsupported youtube target_type: {target_type}")
        channel_id = target_value.strip()
        if not channel_id:
            raise CollectorError("youtube channel_rss requires channel id")
        if not self._api_key:
            return await self._rss.fetch(target_type, target_value, limit)
        try:
            items = await self.fetch_feed(channel_id, limit)
        except CollectorError as exc:
            logger.warning("youtube api failed for %s (%s); falling back to rss", channel_id, exc)
            return await self._rss.fetch(target_type, target_value, limit)
        return FetchResult(platform=self.platform, fetch_kind="youtube_rss", items=items)

    async def fetch_feed(self, channel_id: str, limit: int) -> list[dict]:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id(channel_id),
            "maxResults": str(max(1, min(limit, 50))),
            "key": self._api_key,
        }
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            last_error: Exception | None = None
            for attempt in range(self._max_retries + 1):
                if attempt:
                    import asyncio

                    await asyncio.sleep(min(2**attempt, 8))
                try:
                    resp = await client.get(
                        f"{API_BASE}/playlistItems", params=params, timeout=self._timeout
                    )
                    if resp.status_code in (400, 403, 404):
                        # key 失效 / 配额耗尽 / 频道无上传列表：重试无意义，直接走 RSS 回退
                        detail = resp.text[:200]
                        raise CollectorError(f"youtube api {resp.status_code}: {detail}") from None
                    resp.raise_for_status()
                    break
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    last_error = exc
                    if attempt == self._max_retries:
                        raise CollectorError(f"youtube api failed: {exc}") from exc
            else:
                raise CollectorError(f"youtube api failed: {last_error}")

        payload = resp.json()
        items: list[dict] = []
        for entry in payload.get("items", [])[:limit]:
            snippet = entry.get("snippet") or {}
            details = entry.get("contentDetails") or {}
            video_id = str(details.get("videoId") or "").strip()
            if not video_id:
                continue
            published = snippet.get("publishedAt") or ""
            items.append(
                {
                    "id": video_id,
                    "channel_id": channel_id,
                    "title": snippet.get("title") or "",
                    "link": f"https://www.youtube.com/watch?v={video_id}",
                    "published": published,
                    "updated": details.get("videoPublishedAt") or published,
                    "author": snippet.get("channelTitle") or "",
                    "thumbnail": _best_thumbnail(snippet.get("thumbnails") or {}),
                    "summary": snippet.get("description") or "",
                }
            )
        return items
