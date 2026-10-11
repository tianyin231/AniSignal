"""公开列表的全量排序、分页与指标窗口回归。"""

from datetime import timedelta

from app.clock import utcnow
from app.models import ContentVersion, Event, Evidence, MetricSnapshot, RawContent


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
