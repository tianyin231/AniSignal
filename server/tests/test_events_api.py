"""事件/商品 API 测试（公开 + 管理）。"""

from datetime import timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import Event, Evidence, Product, RawContent
from app.services.events import run_aggregation
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import VIDEO_RAW


async def _prepare(session):
    """两条官方延期公告 + 一个商品（watch 爱弥斯）+ 一条用户开箱。"""
    from app.services.classify import seed_defaults

    await seed_defaults(session)
    session.add(
        Product(slug="kb", name="爱弥斯联名键盘", watch_terms=["爱弥斯"], skus=["标准版"])
    )
    await session.commit()
    await ingest_items(
        session,
        "bilibili_search",
        [
            dict(VIDEO_RAW, bvid="BVe1", title="爱弥斯联名键盘延期发货公告", owner_mid=1955897084, owner_name="鸣潮"),
            dict(VIDEO_RAW, bvid="BVe2", title="爱弥斯键盘开箱体验", owner_mid=777, owner_name="用户A", description=""),
        ],
    )
    await ingest_items(
        session,
        "kuro_notices",
        [
            {
                "platform": "kuro_cn",
                "entry": {"id": "90001", "tabTitle": {"zh-Hans": "延期发货相关公告"}},
                "detail": None,
            }
        ],
    )
    await run_aggregation(session)
    await session.commit()


async def test_public_events_list_and_filter(client, session):
    await _prepare(session)
    resp = await client.get("/api/events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    categories = {item["category"] for item in body["items"]}
    assert "delay" in categories
    assert "unboxing" in categories
    # 事件带商品关联与证据来源
    unboxing = next(i for i in body["items"] if i["category"] == "unboxing")
    assert unboxing["product_slug"] == "kb"
    assert unboxing["evidence_count"] == 1
    assert unboxing["sources"][0]["platform"] == "bilibili"
    # 类目过滤
    resp = await client.get("/api/events", params={"category": "delay"})
    assert all(i["category"] == "delay" for i in resp.json()["items"])


async def test_public_event_detail_with_corrections(client, session):
    await _prepare(session)
    events = resp = (await client.get("/api/events")).json()["items"]
    event_id = events[0]["id"]
    resp = await client.get(f"/api/events/{event_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["corrections"], "引擎创建应写入修订记录"
    assert detail["corrections"][0]["actor"] == "engine"


async def test_public_products_with_stages(client, session):
    await _prepare(session)
    resp = await client.get("/api/products")
    body = resp.json()
    assert len(body["items"]) == 1
    product = body["items"][0]
    assert product["slug"] == "kb"
    assert product["current_stage"] == 2  # 1 个独立用户开箱 → 首位普通用户到货
    labels = [s["label"] for s in product["stages"]]
    assert labels[0] == "尚未发货"
    current = next(s for s in product["stages"] if s["state"] == "current")
    assert current["label"] == "首位普通用户到货"

    resp = await client.get("/api/products/kb")
    assert resp.status_code == 200
    resp = await client.get("/api/products/nope")
    assert resp.status_code == 404


async def test_admin_event_edit_locks_and_revisions(client, session, admin_headers):
    await _prepare(session)
    events = (await client.get("/api/admin/events", headers=admin_headers)).json()["items"]
    event_id = events[0]["id"]

    resp = await client.put(
        f"/api/admin/events/{event_id}",
        json={"title": "人工修正后的标题", "importance": True},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "人工修正后的标题"
    # 人工编辑自动锁定
    event = await session.get(Event, event_id)
    assert event.manual_locked is True
    assert any(r["actor"] == "admin" for r in (await client.get(f"/api/events/{event_id}")).json()["corrections"])


async def test_admin_products_crud(client, session, admin_headers):
    resp = await client.post(
        "/api/admin/products",
        json={"slug": "new-p", "name": "测试商品", "watch_terms": ["测试"]},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    pid = resp.json()["id"]
    resp = await client.put(
        f"/api/admin/products/{pid}", json={"stage_hint": 3}, headers=admin_headers
    )
    assert resp.json()["current_stage"] == 3
    resp = await client.delete(f"/api/admin/products/{pid}", headers=admin_headers)
    assert resp.status_code == 204


async def test_admin_rules_crud_and_recompute(client, session, admin_headers):
    await _prepare(session)
    resp = await client.get("/api/admin/classification-rules", headers=admin_headers)
    rules = resp.json()
    assert len(rules) >= 20  # 种子词典

    resp = await client.post(
        "/api/admin/classification-rules",
        json={"category": "quality", "pattern": "翻车", "priority": 5},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    rule_id = resp.json()["id"]

    resp = await client.post("/api/admin/events/recompute", headers=admin_headers)
    assert resp.status_code == 200
    assert "events_created" in resp.json()

    resp = await client.delete(
        f"/api/admin/classification-rules/{rule_id}", headers=admin_headers
    )
    assert resp.status_code == 204


async def test_event_has_facts(client, session):
    await _prepare(session)
    events = (await client.get("/api/events?limit=5")).json()["items"]
    assert events
    labels = {f["label"] for f in events[0]["facts"]}
    assert "类目" in labels and "证据数" in labels


async def test_public_events_recent_window_uses_evidence_publish_time(client, session):
    await _prepare(session)
    raws = list(await session.scalars(select(RawContent)))
    for raw in raws:
        raw.published_at = utcnow() - timedelta(days=10)

    unboxing = await session.scalar(
        select(Event).where(Event.category == "unboxing", Event.is_active.is_(True))
    )
    recent_raw_id = await session.scalar(
        select(Evidence.raw_content_id).where(Evidence.event_id == unboxing.id)
    )
    recent_raw = await session.get(RawContent, recent_raw_id)
    recent_raw.published_at = utcnow() - timedelta(hours=2)
    await session.commit()

    body = (await client.get("/api/events", params={"published_within_hours": 72})).json()
    assert body["total"] == 1
    assert body["items"][0]["category"] == "unboxing"
