"""跨格式采集、稳定内容身份和私网地址边界。"""

import socket

import pytest

from app.collectors.base import CollectorError
from app.collectors.rss import parse_feed, public_feed_url
from app.parsers.rss import parse_item
from app.services.ai_news import constrain_news


@pytest.mark.parametrize("xml", [
    b'<rss version="2.0"><channel><title>Research</title><item><title>New model</title><link>/release</link><description>&lt;p&gt;Weights released&lt;/p&gt;</description><pubDate>Fri, 23 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>',
    b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Research</title><entry><title>New model</title><link href="/release"/><summary type="html">&lt;p&gt;Weights released&lt;/p&gt;</summary><published>2026-10-23T12:00:00Z</published></entry></feed>',
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"><channel><title>Research</title></channel><item><title>New model</title><link>https://example.org/release</link><description>Weights released</description><date>2026-10-23T12:00:00Z</date></item></rdf:RDF>',
])
def test_feed_formats_share_canonical_identity_and_parser(xml):
    rows = parse_feed(xml, "https://example.org/feed.xml", config={"name": "Lab", "first_party": True})
    normalized = parse_item(rows[0])
    assert normalized.platform == "rss" and normalized.url == "https://example.org/release"
    assert normalized.text == "Weights released"
    assert normalized.author.name == "Lab" and normalized.author.identity == "official"
    assert normalized.published_at.isoformat() == "2026-10-23T12:00:00+00:00"
    assert normalized.external_id == parse_item({**rows[0], "id": "changed-guid"}).external_id


def test_unknown_source_time_is_preserved_and_empty_feed_succeeds():
    rows = parse_feed(b'<rss><channel><item><title>News</title><link>https://example.org/n</link></item></channel></rss>', "https://example.org/rss")
    assert parse_item(rows[0]).published_at is None
    assert parse_feed(b'<rss><channel/></rss>', "https://example.org/rss") == []
    with pytest.raises(CollectorError):
        parse_feed(b'<html><body>Access denied</body></html>', "https://example.org/rss")


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"])
async def test_feed_cannot_fetch_loopback_metadata_or_private_network(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))])
    with pytest.raises(CollectorError, match="私有网络"):
        await public_feed_url("https://example.org/feed")


def test_english_rss_uses_editorial_review_without_chinese_title_gate():
    news = {"newsworthy": True, "headline": "研究团队发布模型", "summary": "来源公布了模型。", "kind": "report", "topics": []}
    assert constrain_news(news, {"platform": "rss", "title": "Introducing our model", "text": "The lab released its research."})["newsworthy"]


async def test_teaser_body_is_reused_without_redownload_or_new_version(session, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import func, select
    from app.models import ContentVersion
    from app.services import rss_body
    from app.services.ingest import ingest_items
    calls = []
    async def fetch(client, url):
        calls.append(url)
        return "Research team released model weights with benchmarks and limitations. " * 12
    monkeypatch.setattr(rss_body, "fetch_body", fetch)
    collector = SimpleNamespace(source_config={}, proxy_url=None)
    xml = b'<rss><channel><item><title>Research</title><link>https://example.org/paper</link><description>Read the full story</description></item></channel></rss>'
    for _ in range(2):
        rows = parse_feed(xml, "https://example.org/feed")
        await rss_body.enrich_items(session, collector, rows)
        assert len(rows[0]["text"]) > 280 and rows[0]["body_extracted"]
        await ingest_items(session, "rss_feed", rows, community="ai")
    assert len(calls) == 1
    assert await session.scalar(select(func.count(ContentVersion.id))) == 1
