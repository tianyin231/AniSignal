"""YouTube Data API 采集器：条目映射、失败回落 RSS、工厂按 key 选择。"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.collectors import youtube_api as youtube_api_module
from app.collectors.base import CollectorError
from app.collectors.youtube_api import YoutubeApiCollector, uploads_playlist_id
from app.config import get_settings


def _api_payload():
    return {
        "items": [
            {
                "snippet": {
                    "title": "Official Trailer",
                    "description": "Fresh trailer description",
                    "publishedAt": "2026-09-12T10:00:00Z",
                    "channelTitle": "Wuthering Waves 官方频道",
                    "thumbnails": {
                        "default": {"url": "https://i.ytimg.com/vi/abc/default.jpg"},
                        "high": {"url": "https://i.ytimg.com/vi/abc/hq.jpg"},
                    },
                },
                "contentDetails": {"videoId": "abc123", "videoPublishedAt": "2026-09-12T10:00:00Z"},
            }
        ]
    }


@pytest.fixture
def api_collector(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "youtube_api_key", "test-key")
    return YoutubeApiCollector()


async def test_fetch_maps_api_items_to_rss_shape(api_collector, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_api_payload())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        youtube_api_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await api_collector.fetch("channel_rss", "UC0Bi5KMcECRVYis5Gb_ZYZQ", 10)

    assert result.fetch_kind == "youtube_rss" and result.platform.value == "youtube"
    item = result.items[0]
    assert item["id"] == "abc123"
    assert item["channel_id"] == "UC0Bi5KMcECRVYis5Gb_ZYZQ"
    assert item["title"] == "Official Trailer"
    assert item["link"] == "https://www.youtube.com/watch?v=abc123"
    assert item["published"] == "2026-09-12T10:00:00Z"
    assert item["author"] == "Wuthering Waves 官方频道"
    assert item["thumbnail"] == "https://i.ytimg.com/vi/abc/hq.jpg"
    assert item["summary"] == "Fresh trailer description"
    assert "playlistItems" in calls[0] and "UU0Bi5KMcECRVYis5Gb_ZYZQ" in calls[0]


async def test_api_failure_falls_back_to_rss(api_collector, monkeypatch):
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"reason": "quotaExceeded"}})

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <entry><yt:videoId>rssvid1</yt:videoId><title>From RSS</title>
        <link rel="alternate" href="https://www.youtube.com/watch?v=rssvid1"/>
        <published>2026-09-12T09:00:00+00:00</published><updated>2026-09-12T09:00:00+00:00</updated>
        <author><name>Channel</name></author></entry>
    </feed>"""

    real_client = httpx.AsyncClient

    def route(url: str) -> httpx.Response:
        if "googleapis.com" in url:
            return httpx.Response(403, json={"error": {"reason": "quotaExceeded"}})
        return httpx.Response(200, text=rss_xml)

    def make_client(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda request: route(str(request.url))), **kwargs)

    monkeypatch.setattr(youtube_api_module.httpx, "AsyncClient", make_client)
    import app.collectors.youtube_rss as rss_module

    monkeypatch.setattr(rss_module.httpx, "AsyncClient", make_client)

    result = await api_collector.fetch("channel_rss", "UC0Bi5KMcECRVYis5Gb_ZYZQ", 10)
    assert result.items[0]["id"] == "rssvid1"
    assert result.items[0]["title"] == "From RSS"


async def test_no_key_delegates_to_rss(api_collector):
    api_collector._api_key = ""
    rss = AsyncMock()
    rss.fetch = AsyncMock(return_value="rss-result")
    api_collector._rss = rss
    assert await api_collector.fetch("channel_rss", "UCx", 5) == "rss-result"
    rss.fetch.assert_awaited_once_with("channel_rss", "UCx", 5)


def test_uploads_playlist_id_rejects_non_channel():
    from app.collectors.base import CollectorError as CE

    try:
        uploads_playlist_id("615344025")
        raise AssertionError("should reject")
    except CE:
        pass


