import httpx
import pytest

from app.collectors import youtube_rss as youtube_module


async def test_youtube_rss_reads_attribute_urls_and_channel_id(monkeypatch):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/">
      <entry>
        <yt:videoId>abcdefghijk</yt:videoId>
        <title>Official Trailer</title>
        <link rel="alternate" href="https://www.youtube.com/watch?v=abcdefghijk" />
        <published>2026-08-23T01:00:00+00:00</published>
        <updated>2026-08-23T01:01:00+00:00</updated>
        <author><name>Wuthering Waves</name></author>
        <media:group>
          <media:thumbnail url="https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg" />
          <media:description>Video description</media:description>
        </media:group>
      </entry>
    </feed>"""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    monkeypatch.setattr(
        youtube_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    rows = await youtube_module.YoutubeRssCollector().fetch_feed("UCofficial", 10)

    assert rows == [
        {
            "id": "abcdefghijk",
            "channel_id": "UCofficial",
            "title": "Official Trailer",
            "link": "https://www.youtube.com/watch?v=abcdefghijk",
            "published": "2026-08-23T01:00:00+00:00",
            "updated": "2026-08-23T01:01:00+00:00",
            "author": "Wuthering Waves",
            "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
            "summary": "Video description",
        }
    ]


async def test_youtube_rss_retries_transient_404(monkeypatch):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns:media="http://search.yahoo.com/mrss/">
      <entry><yt:videoId>abcdefghijk</yt:videoId><title>Recovered</title></entry>
    </feed>"""
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(404 if attempts < 3 else 200, text=xml)

    async def no_sleep(_seconds):
        return None

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        youtube_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(youtube_module.asyncio, "sleep", no_sleep)

    rows = await youtube_module.YoutubeRssCollector().fetch_feed("UCofficial", 10)

    assert attempts == 3
    assert rows[0]["title"] == "Recovered"


async def test_youtube_rss_does_not_retry_other_4xx(monkeypatch):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(403)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        youtube_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await youtube_module.YoutubeRssCollector().fetch_feed("UCofficial", 10)

    assert attempts == 1
