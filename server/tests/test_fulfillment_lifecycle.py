"""履约从采集配置到证据撤销的业务回归。"""

from sqlalchemy import func, select

from app.models import CollectTarget, Event, Evidence, Product, ProductFulfillmentRevision, ProductSignal, RawContent
from app.services.events import run_aggregation
from tests.test_events_api import _prepare


async def _verify_receipt(client, session, admin_headers):
    await _prepare(session)
    product = await session.scalar(select(Product))
    signal = await session.scalar(select(ProductSignal).where(ProductSignal.signal_type == "consumer_received"))
    response = await client.patch(f"/api/admin/products/{product.id}/signals/{signal.id}", json={
        "review_status": "verified", "actor_type": "user", "acquisition_type": "self_purchase",
        "claim_quote": "本人自购订单已到货", "note": "测试核验",
    }, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert (await client.get("/api/products/kb")).json()["fulfillment"]["consumer"]["verified_count"] == 1
    return product


async def test_product_terms_change_removes_stale_verified_evidence(client, session, admin_headers):
    product = await _verify_receipt(client, session, admin_headers)
    response = await client.put(f"/api/admin/products/{product.id}", json={"watch_terms": ["另一件完全不同的商品"]}, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["fulfillment"]["consumer"]["verified_count"] == 0
    assert data["fulfillment"]["official"]["verified_count"] == 0
    assert data["evidences"] == []
    assert await session.scalar(select(func.count(Event.id)).where(Event.product_id == product.id, Event.is_active.is_(True))) == 0
    latest = await session.scalar(select(ProductFulfillmentRevision).where(ProductFulfillmentRevision.product_id == product.id).order_by(ProductFulfillmentRevision.id.desc()))
    assert latest.snapshot["consumer"]["verified_count"] == 0


async def test_product_community_change_cannot_relabel_old_signals(client, session, admin_headers):
    product = await _verify_receipt(client, session, admin_headers)
    response = await client.put(f"/api/admin/products/{product.id}", json={"community": "hsr"}, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["community"] == "hsr"
    assert response.json()["fulfillment"]["candidate_total"] == 0
    assert response.json()["fulfillment"]["consumer"]["verified_count"] == 0
    signals = list(await session.scalars(select(ProductSignal)))
    assert signals and all(signal.community == "wuwa" and not signal.active for signal in signals)
    assert all(raw.community == "wuwa" for raw in await session.scalars(select(RawContent)))


async def test_excluding_verified_evidence_updates_state_and_revision_immediately(client, session, admin_headers):
    product = await _verify_receipt(client, session, admin_headers)
    event = await session.scalar(select(Event).where(Event.product_id == product.id, Event.category == "unboxing"))
    evidence = await session.scalar(select(Evidence).where(Evidence.event_id == event.id))
    response = await client.delete(f"/api/admin/events/{event.id}/evidences/{evidence.raw_content_id}", headers=admin_headers)
    assert response.status_code == 200, response.text
    product_data = (await client.get("/api/products/kb")).json()
    assert product_data["fulfillment"]["consumer"]["verified_count"] == 0
    revision = await session.scalar(select(ProductFulfillmentRevision).order_by(ProductFulfillmentRevision.id.desc()))
    assert revision.previous_snapshot["consumer"]["verified_count"] == 1
    assert revision.snapshot["consumer"]["verified_count"] == 0


async def test_hidden_and_restored_evidence_updates_fulfillment(client, session, admin_headers):
    await _verify_receipt(client, session, admin_headers)
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVe2"))
    for hidden, count in [(True, 0), (False, 1)]:
        response = await client.patch(f"/api/admin/contents/{raw.id}/visibility", json={"hidden": hidden}, headers=admin_headers)
        assert response.status_code == 200, response.text
        assert (await client.get("/api/products/kb")).json()["fulfillment"]["consumer"]["verified_count"] == count
        revision = await session.scalar(select(ProductFulfillmentRevision).order_by(ProductFulfillmentRevision.id.desc()))
        assert revision.snapshot["consumer"]["verified_count"] == count
        if hidden:
            await run_aggregation(session)
            await session.commit()


async def test_deleted_last_receipt_creates_correction(client, session, admin_headers):
    await _verify_receipt(client, session, admin_headers)
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVe2"))
    response = await client.delete(f"/api/admin/contents/{raw.id}", headers=admin_headers)
    assert response.status_code == 204
    assert (await client.get("/api/products/kb")).json()["fulfillment"]["consumer"]["verified_count"] == 0
    revision = await session.scalar(select(ProductFulfillmentRevision).order_by(ProductFulfillmentRevision.id.desc()))
    assert revision.snapshot["consumer"]["verified_count"] == 0


async def test_unchanged_aggregation_preserves_signal_timestamp(client, session, admin_headers):
    product = await _verify_receipt(client, session, admin_headers)
    original = (await client.get("/api/products/kb")).json()["fulfillment"]["updated_at"]
    revisions = await session.scalar(select(func.count(ProductFulfillmentRevision.id)))
    await run_aggregation(session)
    await session.commit()
    assert (await client.get("/api/products/kb")).json()["fulfillment"]["updated_at"] == original
    assert await session.scalar(select(func.count(ProductFulfillmentRevision.id))) == revisions


async def test_manual_event_deactivation_survives_aggregation(client, session, admin_headers):
    product = await _verify_receipt(client, session, admin_headers)
    event = await session.scalar(select(Event).where(Event.product_id == product.id, Event.category == "unboxing"))
    response = await client.put(f"/api/admin/events/{event.id}", json={"is_active": False}, headers=admin_headers)
    assert response.status_code == 200, response.text
    await run_aggregation(session)
    await session.commit()
    assert (await client.get("/api/products/kb")).json()["fulfillment"]["consumer"]["verified_count"] == 0


async def test_cross_community_monitor_conflict_is_explicit(client, session, admin_headers):
    first = await client.post("/api/admin/products", json={"name": "同名联名键盘", "community": "wuwa"}, headers=admin_headers)
    assert first.status_code == 201, first.text
    second = await client.post("/api/admin/products", json={"name": "同名联名键盘", "community": "hsr"}, headers=admin_headers)
    assert second.status_code == 409
    assert "其他分区" in second.json()["detail"]
    assert await session.scalar(select(func.count(Product.id))) == 1
    third = await client.post("/api/admin/products", json={"name": "同名联名键盘", "community": "hsr", "monitor_keyword": "星铁 同名联名键盘"}, headers=admin_headers)
    assert third.status_code == 201, third.text
    assert third.json()["monitor"]["target_id"] != first.json()["monitor"]["target_id"]


async def test_monitor_edit_changes_only_its_owned_target(client, session, admin_headers):
    created = await client.post("/api/admin/products", json={"name": "爱弥斯键盘", "community": "wuwa"}, headers=admin_headers)
    assert created.status_code == 201, created.text
    product = created.json()
    original_id = product["monitor"]["target_id"]
    edited = await client.put(f"/api/admin/products/{product['id']}", json={
        "monitor_keyword": "鸣潮 爱弥斯键盘", "monitor_enabled": False,
        "monitor_interval_minutes": 60, "monitor_min_views": 100,
    }, headers=admin_headers)
    assert edited.status_code == 200, edited.text
    monitor = edited.json()["monitor"]
    assert monitor["target_id"] != original_id
    assert monitor["enabled"] is False and monitor["interval_minutes"] == 60 and monitor["min_views"] == 100
    assert not (await session.get(CollectTarget, original_id)).enabled
    assert (await session.get(CollectTarget, monitor["target_id"])).community == "wuwa"


async def test_multiaxis_content_gets_receipt_and_quality_candidates(client, session, admin_headers):
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import VIDEO_RAW

    session.add(Product(slug="mixed", name="爱弥斯键盘", watch_terms=["爱弥斯键盘"]))
    await session.commit()
    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, bvid="BV-mixed", title="爱弥斯 键盘开箱，品控拉完了", description="按键故障", owner_mid=778)])
    await run_aggregation(session)
    await session.commit()
    product = await session.scalar(select(Product).where(Product.slug == "mixed"))
    data = next(item for item in (await client.get("/api/admin/products", headers=admin_headers)).json()["items"] if item["id"] == product.id)
    assert data["fulfillment"]["consumer"]["candidate_count"] == 1
    assert data["fulfillment"]["quality"]["candidate_count"] == 1
    assert data["fulfillment"]["consumer"]["verified_count"] == 0
