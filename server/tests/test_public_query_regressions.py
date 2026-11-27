"""公开列表的全量排序、分页与指标窗口回归。"""

from datetime import timedelta

from sqlalchemy import event, select

from app.api import public
from app.clock import utcnow
from app.models import ContentReview, ContentVersion, Event, Evidence, MetricSnapshot, Product, RawContent, Source


async def add_content(session, name, *, age=0, community="wuwa", hidden=False, views=None):
    raw = RawContent(
        platform="bilibili", content_type="video", external_id=name, community=community,
        hidden=hidden, published_at=utcnow() - timedelta(minutes=age),
    )
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title=name, category="official_announce"))
    if views is not None:
        session.add(MetricSnapshot(raw_content_id=raw.id, metrics={"view": views}))
    return raw


async def test_hot_ranks_all_contents_and_paginates_past_200(client, session):
    for i in range(205):
        await add_content(session, f"video-{i}", age=i, views=1_000_000 if i == 204 else i)
    await session.commit()

    first = (await client.get("/api/contents", params={"sort": "hot", "limit": 2})).json()
    assert first["total"] == 205
    assert [item["external_id"] for item in first["items"]] == ["video-204", "video-203"]
    last = (await client.get("/api/contents", params={"sort": "hot", "offset": 200, "limit": 10})).json()
    assert last["total"] == 205
    assert [item["external_id"] for item in last["items"]] == [f"video-{i}" for i in range(4, -1, -1)]


async def test_hot_uses_latest_numeric_views_and_keeps_missing_metrics(client, session):
    older = await add_content(session, "latest-decreased", views=5000)
    await session.flush()
    session.add(MetricSnapshot(raw_content_id=older.id, metrics={"view": 9}))
    await add_content(session, "numeric-string", views="100")
    await add_content(session, "without-metrics")
    await add_content(session, "hidden", hidden=True, views=999999)
    await add_content(session, "other-community", community="ai", views=999999)
    await session.commit()

    body = (await client.get("/api/contents", params={"sort": "hot"})).json()
    assert body["total"] == 3
    assert [item["external_id"] for item in body["items"]] == ["numeric-string", "latest-decreased", "without-metrics"]
    assert body["items"][1]["metrics"]["view"] == 9
    assert body["items"][2]["metrics"] is None


async def test_trending_limits_after_delta_and_visibility_filters(client, session):
    now = utcnow()
    cases = [
        ("largest", 100, 200, {}),
        ("numeric-string", "2", "12", {}),
        ("missing-first-view", None, 20, {}),
        ("decreased", 100, 50, {}),
        ("hidden", 0, 999999, {"hidden": True}),
        ("other-community", 0, 999999, {"community": "ai"}),
    ]
    for name, first, last, kwargs in cases:
        raw = await add_content(session, name, **kwargs)
        for hours, value in [(48, 0), (12, first), (1, last)]:
            session.add(MetricSnapshot(raw_content_id=raw.id, metrics={} if value is None else {"view": value},
                                       captured_at=now - timedelta(hours=hours)))
    await add_content(session, "only-one-snapshot", views=999999)
    await session.commit()

    body = (await client.get("/api/trending", params={"hours": 24, "limit": 2})).json()
    assert [(item["external_id"], item["delta_view"]) for item in body["items"]] == [
        ("largest", 100), ("missing-first-view", 20),
    ]
    body = (await client.get("/api/trending", params={"hours": 24, "limit": 10})).json()
    assert [item["delta_view"] for item in body["items"]] == [100, 20, 10]


async def test_event_total_uses_same_visible_evidence_scope_as_page(client, session):
    for name in ("visible", "hidden", "excluded", "empty"):
        event = Event(title=name, community="wuwa", category="official_announce", is_active=True)
        session.add(event)
        await session.flush()
        if name != "empty":
            raw = await add_content(session, name, hidden=name == "hidden")
            session.add(Evidence(event_id=event.id, raw_content_id=raw.id,
                                 role="excluded" if name == "excluded" else "support"))
    await session.commit()

    body = (await client.get("/api/events", params={"limit": 1})).json()
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["visible"]
    last = (await client.get("/api/events", params={"offset": 1})).json()
    assert last["total"] == 1
    assert last["items"] == []


async def test_contents_page_and_total_follow_the_latest_version(client, session):
    """分页 id 与总数分开查询，两边都必须只看最新版本的分类。"""
    demoted = await add_content(session, "demoted")
    session.add(ContentVersion(raw_content_id=demoted.id, version=2, title="demoted", category="chat"))
    promoted = await add_content(session, "promoted", age=5)
    promoted_v1 = await session.scalar(select(ContentVersion).where(ContentVersion.raw_content_id == promoted.id))
    promoted_v1.category = "chat"
    session.add(ContentVersion(raw_content_id=promoted.id, version=2, title="新标题", category="official_announce"))
    await add_content(session, "hidden", hidden=True)
    await session.commit()

    body = (await client.get("/api/contents")).json()
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["新标题"]


async def test_hot_without_window_only_ranks_recent_week(client, session):
    await add_content(session, "old-viral", age=8 * 24 * 60, views=10_000_000)
    await add_content(session, "recent", age=60, views=10)
    await session.commit()

    body = (await client.get("/api/contents", params={"sort": "hot"})).json()
    assert body["total"] == 1
    assert [item["external_id"] for item in body["items"]] == ["recent"]
    wider = (await client.get("/api/contents", params={"sort": "hot", "published_within_hours": 8760})).json()
    assert [item["external_id"] for item in wider["items"]] == ["old-viral", "recent"]


async def test_sitemap_lists_public_pages_and_skips_inactive_or_ai_events(client, session):
    public._SITEMAP_CACHE.clear()
    session.add_all([
        Product(slug="figure-a", name="手办 A", community="wuwa"),
        Event(title="进行中", category="official_announce", community="wuwa", is_active=True),
        Event(title="已关闭", category="official_announce", community="wuwa", is_active=False),
        Event(title="AI 事件", category="ai_news", community="ai", is_active=True),
    ])
    await session.commit()
    active = await session.scalar(select(Event).where(Event.title == "进行中"))
    closed = await session.scalar(select(Event).where(Event.title == "已关闭"))
    news = await session.scalar(select(Event).where(Event.title == "AI 事件"))

    response = await client.get("/api/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "/products/figure-a?community=wuwa</loc>" in response.text
    assert "/?community=wuwa</loc>" in response.text
    assert "/?community=ai</loc>" not in response.text
    assert f"/events/{active.id}</loc>" in response.text
    assert f"/events/{closed.id}</loc>" not in response.text
    assert f"/events/{news.id}</loc>" not in response.text
    public._SITEMAP_CACHE.clear()


async def test_ordered_public_queries_avoid_wide_blob_columns_in_sort_buffer(client, session):
    """回归：首页精选、主题流总览、话题筛选、外链榜与热度飙升排序阶段不得选大 JSON/TEXT 列。

    MySQL 8.4 默认 sort_buffer_size 为 256 KiB；在带 ORDER BY 的语句里直选 raw_json /
    normalized_json / content_versions.text 会触发 OperationalError(1038, Out of sort memory)。
    """
    now = utcnow()
    official = Source(platform="bilibili", external_id="off-1", name="官方账号", identity="official", raw_json={"big": "x" * 2048})
    session.add(official)
    await session.flush()

    raw = RawContent(
        platform="bilibili",
        content_type="post",
        external_id="highlight-wide-1",
        community="wuwa",
        source_id=official.id,
        url="https://example.com/post/1",
        published_at=now - timedelta(hours=1),
        raw_json={"payload": "a" * 4096},
    )
    session.add(raw)
    await session.flush()
    session.add(
        ContentVersion(
            raw_content_id=raw.id,
            version=1,
            title="官方版本前瞻",
            text="查看活动详情 https://example.com/event-demo " + ("长正文" * 200),
            normalized_json={
                "media": ["https://example.com/cover.jpg"],
                "extra": {"topics": ["前瞻"]},
            },
            category="official_announce",
        )
    )
    session.add(
        ContentReview(
            raw_content_id=raw.id,
            relevant=True,
            promotional=False,
            violation=False,
            quality_score=90,
            links=[{"url": "https://example.com/ai-event", "label": "活动专题页"}],
            model="test",
        )
    )
    session.add_all(
        [
            MetricSnapshot(raw_content_id=raw.id, metrics={"view": 100}, captured_at=now - timedelta(hours=6)),
            MetricSnapshot(raw_content_id=raw.id, metrics={"view": 900}, captured_at=now - timedelta(minutes=10)),
        ]
    )
    await session.commit()

    captured_sql: list[str] = []
    engine = session.bind.sync_engine

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        captured_sql.append(statement)

    public._official_links_cache.clear()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        r_highlights = await client.get("/api/highlights", params={"community": "all", "limit": 5})
        r_overview = await client.get("/api/feeds/overview", params={"community": "wuwa"})
        r_topic = await client.get("/api/contents", params={"community": "wuwa", "topic": "前瞻"})
        r_trending = await client.get("/api/trending", params={"community": "wuwa", "hours": 24, "limit": 5})
        r_links = await client.get("/api/official-links", params={"community": "wuwa"})
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        public._official_links_cache.clear()

    assert r_highlights.status_code == 200
    assert [item["external_id"] for item in r_highlights.json()["items"]] == ["highlight-wide-1"]
    assert r_overview.status_code == 200
    assert r_overview.json()["total_count"] == 1
    assert r_topic.status_code == 200
    assert [item["external_id"] for item in r_topic.json()["items"]] == ["highlight-wide-1"]
    assert r_trending.status_code == 200
    assert [item["external_id"] for item in r_trending.json()["items"]] == ["highlight-wide-1"]
    assert r_links.status_code == 200
    assert any(item["url"] == "https://example.com/ai-event" for item in r_links.json()["items"])

    forbidden_in_sort = (
        "raw_contents.raw_json",
        "sources.raw_json",
        "content_versions.normalized_json",
        "content_versions.text",
        "content_reviews.news",
        "content_reviews.links",
        "content_reviews.fulfillment",
        "metric_snapshots.metrics",
    )
    for sql in captured_sql:
        upper = sql.upper()
        if "ORDER BY" not in upper or "RAW_CONTENTS" not in upper:
            continue
        select_head = sql.split("FROM", 1)[0].lower()
        for col in forbidden_in_sort:
            assert col not in select_head, f"Wide column {col} selected in ORDER BY query: {sql}"
