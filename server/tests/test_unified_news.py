"""验证旧数据无损接入、地址兼容、撤稿和机器人快照失效。"""

from datetime import timedelta
import gzip
import json

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.config import get_settings
from app.models import CollectTarget, ContentVersion, NewsArchive, RawContent, Source
from app.services import aihot, native_news
from app.services.news_import import import_news


@pytest.fixture
def snapshot(tmp_path):
    now = utcnow()
    at = (now - timedelta(hours=1)).isoformat() + "Z"
    source = {"id": "rss-lab", "name": "Lab", "kind": "rss", "config": {"feedUrl": "https://example.org/rss"},
        "tier": "T1", "first_party": True, "owner_entity_id": "lab", "interval_minutes": 60,
        "site_fulltext": False, "syndicate_fulltext": False, "participation_mode": "editorial", "enabled": True,
        "created_at": at, "last_ok_at": at}
    external = {**source, "id": "anisignal-bilibili-7", "name": "Author", "kind": "external", "config": {}}
    article = {"id": "as-42", "source_id": external["id"], "title": "Source title", "url": "https://example.org/news",
        "author": "123", "raw": {"platform": "bilibili", "externalId": "video-42"}, "revision": 3,
        "body_status": "none", "media": [], "published_at": at, "discovered_at": at, "updated_at": at}
    rss = {**article, "id": "rss-native", "source_id": source["id"], "raw": {}, "title": "Research", "author": "Lab",
        "url": "https://example.org/research", "body_text": "Research details", "published_at": None}
    publication = {"article_id": article["id"], "analysis_id": 1, "revision": 3, "visibility": "public", "eligible": True,
        "selected": True, "seat": True, "title": "Model release", "summary": "The source announced its model.",
        "score": 90, "tags": ["Codex"], "visible_after": at, "selected_ready_at": at, "updated_at": at,
        "fact_id": 9, "story_id": 8, "body_mode": "summary-only", "backfill": False}
    tables = {"sources": [source, external], "articles": [article, rss],
        "publications": [publication, {**publication, "article_id": rss["id"], "analysis_id": 2, "selected": False}],
        "analyses": [{"id": i, "model": "original-model", "relevance": "pass"} for i in [1, 2]],
        "facts": [{"id": 9, "public_id": "fact-9", "story_id": 8, "title": "Model release", "created_at": at}],
        "stories": [{"id": 8, "public_id": "story-8", "title": "Model release", "digest": "Published digest"}],
        "fact_articles": [{"id": 1, "fact_id": 9, "article_id": article["id"], "role": "primary"},
            {"id": 2, "fact_id": 9, "article_id": rss["id"], "role": "report"}],
        "reports": [{"id": 1, "kind": "daily", "key": str(now.date()), "generated_at": at,
            "content": {"leadItemId": "as-42", "lead": {"title": "Lead", "leadParagraph": "Source claim"},
                "highlights": ["as-42"], "sections": [], "flashes": []}}]}
    path = tmp_path / "news.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as file:
        file.write(json.dumps({"format": "anisignal-news-v1"}) + "\n")
        for table, rows in tables.items():
            for row in rows:
                file.write(json.dumps({"table": table, "row": row}) + "\n")
    return path


@pytest.fixture
def unified(monkeypatch, session_factory):
    monkeypatch.setattr(get_settings(), "news_unified_enabled", True)
    monkeypatch.setattr(native_news, "SessionLocal", session_factory)


async def test_import_reuses_existing_raw_and_does_not_repeat_history(session, snapshot):
    session.add(Source(id=7, platform="bilibili", external_id="123", name="Author"))
    session.add(RawContent(id=42, platform="bilibili", external_id="video-42", community="ai", content_type="video",
        content_hash="original-hash", raw_json={"immutable": True}, published_at=utcnow() - timedelta(hours=1)))
    session.add(ContentVersion(raw_content_id=42, version=2, title="Original title", text="Original text", normalized_json={}))
    await session.commit()
    await import_news(session, snapshot)
    assert (await session.get(RawContent, 42)).raw_json == {"immutable": True}
    assert await session.scalar(select(func.count(ContentVersion.id)).where(ContentVersion.raw_content_id == 42)) == 1
    alias = await session.get(NewsArchive, ("article", "as-42"))
    assert alias.raw_content_id == 42 and alias.payload["content_version"] == 2
    before = await session.scalar(select(func.count(NewsArchive.key)))
    assert (await import_news(session, snapshot))["replayed"]
    assert await session.scalar(select(func.count(NewsArchive.key))) == before
    assert await session.scalar(select(func.count(CollectTarget.id))) == 1


async def test_old_public_urls_and_unknown_time_work_without_old_service(client, session, snapshot, unified, monkeypatch):
    await import_news(session, snapshot)
    def no_network(*args, **kwargs):
        raise AssertionError("统一读取不应访问旧服务")
    monkeypatch.setattr(aihot.httpx, "AsyncClient", no_network)
    feed = await client.get("/api/ai-news/events")
    assert feed.status_code == 200 and feed.json()["engine"] == "anisignal"
    assert feed.json()["total"] == 2
    assert len(feed.json()["sources"]) == 1
    detail = await client.get("/api/ai-news/engine/items/as-42")
    assert detail.status_code == 200 and detail.json()["title"] == "Model release"
    unknown = await client.get("/api/ai-news/engine/items/rss-native")
    assert unknown.status_code == 200 and unknown.json()["publishedAt"] is None
    story = await client.get("/api/ai-news/engine/stories/story-8")
    assert story.status_code == 200 and story.json()["reportCount"] == 2
    reports = await client.get("/api/ai-news/engine/reports/daily")
    assert reports.status_code == 200 and len(reports.json()["items"]) == 1


async def test_withdrawal_invalidates_bot_snapshot_and_report_claims(client, session, snapshot, unified):
    await import_news(session, snapshot)
    now = utcnow()
    items = await aihot.bot_items([], now - timedelta(days=1), now)
    assert len(items) == 1 and items[0]["news"]["fact_id"] == 9
    assert await aihot.validate_bot_items(items) == {"aihot:as-42"}
    alias = await session.get(NewsArchive, ("article", "as-42"))
    raw = await session.get(RawContent, alias.raw_content_id)
    raw.hidden = True
    await session.commit()
    assert await aihot.validate_bot_items(items) == set()
    assert (await client.get("/api/ai-news/engine/items/as-42")).status_code == 404
    issue = await client.get("/api/ai-news/engine/reports/daily/" + str(now.date()))
    assert issue.status_code == 200
    assert issue.json()["highlights"][0]["available"] is False and issue.json()["lead"] is None


async def test_admin_uses_main_database_and_keeps_shared_auth(client, session, snapshot, unified, admin_headers):
    await import_news(session, snapshot)
    assert (await client.get("/api/admin/aihot/status")).status_code == 422
    response = await client.get("/api/admin/aihot/status", headers=admin_headers)
    assert response.status_code == 200 and response.json()["engine"] == "anisignal"
    assert response.json()["sources"]["totals"]["total"] == 1
    response = await client.get("/api/admin/aihot/content/as-42", headers=admin_headers)
    assert response.status_code == 200
    version = response.json()["override"]["version"]
    response = await client.post("/api/admin/aihot/content/as-42/visibility", headers=admin_headers,
        json={"visibility": "withdrawn", "version": version, "reason": "Correction"})
    assert response.status_code == 200
    assert (await client.get("/api/ai-news/engine/items/as-42")).status_code == 404
