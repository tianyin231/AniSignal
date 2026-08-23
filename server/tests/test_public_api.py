"""公开 API 单测：内容流（过滤/搜索/摘要）、来源健康（不泄露内部字段）、统计。"""

from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import CollectTarget, RawContent
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import DYNAMIC_RAW, VIDEO_RAW
from tests.test_parsers_others import KURO_ITEM


async def _seed(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await ingest_items(session, "kuro_notices", [KURO_ITEM])
    session.add(
        CollectTarget(
            platform="bilibili",
            target_type="keyword_search",
            target_value="鸣潮",
            display_name="B站搜索·鸣潮",
        )
    )
    await session.commit()


async def test_public_contents_no_auth(client, session):
    await _seed(session)
    resp = await client.get("/api/contents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    ids = {i["external_id"] for i in body["items"]}
    assert ids == {"BV1Mcgp6ME1x", "50553"}


async def test_public_contents_identity_filter(client, session):
    await _seed(session)
    resp = await client.get("/api/contents", params={"identity": "official"})
    body = resp.json()
    # kuro 公告 identity=official（作者 Kuro Games）；B 站视频作者 unknown
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "50553"
    assert body["items"][0]["identity"] == "official"
    # kuro 公告无指标 → metrics 为 None 不报错
    assert body["items"][0]["metrics"] is None


async def test_public_contents_search_and_fields(client, session):
    await _seed(session)
    resp = await client.get("/api/contents", params={"q": "Version Update"})
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "Version Update Notice"
    assert item["text_excerpt"].startswith("html body")
    assert item["cover"] == "https://example.com/banner.png"
    assert item["source_name"] == "Kuro Games"
    assert item["identity"] == "official"
    assert item["category"] == "official_announce"
    assert item["preview_mode"] == "full"
    assert "<p>" not in item["text_excerpt"]

    # 平台过滤
    resp = await client.get("/api/contents", params={"platform": "bilibili"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["metrics"]["view"] == 680


async def test_public_content_marks_external_only_preview(client, session):
    await ingest_items(session, "bilibili_dynamics", [DYNAMIC_RAW])
    resp = await client.get("/api/contents", params={"identity": "official"})
    item = resp.json()["items"][0]
    assert item["preview_mode"] == "external_only"
    assert item["text_excerpt"] is None
    detail = (await client.get(f"/api/contents/{item['id']}")).json()
    assert detail["preview_mode"] == "external_only"
    assert detail["url"].startswith("https://t.bilibili.com/")


async def test_public_sources_hide_internal_details(client, session):
    await _seed(session)
    resp = await client.get("/api/sources")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    source = items[0]
    assert source["name"] == "B站搜索·鸣潮"
    assert "last_error" not in source
    assert "target_value" not in source


async def test_public_stats(client, session):
    await _seed(session)
    resp = await client.get("/api/stats")
    body = resp.json()
    assert body["total_contents"] == 2
    assert body["by_platform"] == {"bilibili": 1, "kuro_global": 1}
    assert body["official_contents"] == 1
    assert body["latest_collected_at"] is not None


async def test_public_contents_recent_window_and_identity_exclusion(client, session):
    await _seed(session)
    rows = list(await session.scalars(select(RawContent).order_by(RawContent.id)))
    rows[0].published_at = utcnow() - timedelta(days=10)
    rows[1].published_at = utcnow() - timedelta(hours=2)
    await session.commit()

    resp = await client.get(
        "/api/contents",
        params={"published_within_hours": 72, "exclude_identity": "official"},
    )
    body = resp.json()
    assert body["total"] == 0

    resp = await client.get("/api/contents", params={"published_within_hours": 72})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "50553"
