"""履约修复的独立回归：跨轴停用、隐藏后的排除与重复聚合。"""

from sqlalchemy import select

from app.api.admin import set_content_visibility, update_event, update_product
from app.models import CollectTarget, ContentVersion, Event, Evidence, Product, ProductSignal
from app.schemas.api import EventUpdate, ProductUpdate
from app.services.events import exclude_evidence, run_aggregation
from app.services.fulfillment import build_fulfillment_snapshot, review_product_signal
from app.services.ingest import ingest_items
from tests.test_events_api import _prepare
from tests.test_parsers_bilibili import VIDEO_RAW


async def test_disabling_receipt_event_is_not_bypassed_by_quality_axis(session):
    await _prepare(session)
    version = await session.scalar(select(ContentVersion).where(ContentVersion.title == "爱弥斯键盘开箱体验"))
    version.text = "开箱时发现质量故障"
    await run_aggregation(session)
    await session.commit()
    product = await session.scalar(select(Product))
    signal = await session.scalar(select(ProductSignal).where(ProductSignal.signal_type == "consumer_received"))
    await review_product_signal(
        session, product.id, signal.id, review_status="verified", actor_type="user",
        acquisition_type="self_purchase", summary=None, claim_quote=None, note="核验自购",
    )
    await session.commit()
    event = await session.scalar(select(Event).where(Event.product_id == product.id, Event.category == "unboxing"))
    await update_event(event.id, EventUpdate(is_active=False), session)
    await run_aggregation(session)
    await session.commit()
    snapshot = await build_fulfillment_snapshot(session, product.id)
    assert snapshot["consumer"]["verified_count"] == 0
    assert snapshot["quality"]["candidate_count"] == 1


async def test_excluding_last_visible_evidence_after_sibling_is_hidden(session):
    await _prepare(session)
    await ingest_items(session, "bilibili_search", [dict(
        VIDEO_RAW, bvid="BVextraAudit", title="爱弥斯键盘开箱体验二", description="",
        owner_mid=778, owner_name="用户B",
    )])
    await run_aggregation(session)
    await session.commit()
    product = await session.scalar(select(Product))
    event = await session.scalar(select(Event).where(Event.product_id == product.id, Event.category == "unboxing"))
    ids = list(await session.scalars(select(Evidence.raw_content_id).where(Evidence.event_id == event.id)))
    assert len(ids) == 2
    await set_content_visibility(ids[0], {"hidden": True}, session, None)
    await exclude_evidence(session, event.id, ids[1])
    await session.commit()
    assert not event.is_active
    assert (await build_fulfillment_snapshot(session, product.id))["consumer"]["candidate_count"] == 0


async def test_inactive_locked_empty_product_event_is_idempotent(session):
    await _prepare(session)
    product = await session.scalar(select(Product))
    event = await session.scalar(select(Event).where(Event.product_id == product.id, Event.category == "unboxing"))
    await update_event(event.id, EventUpdate(is_active=False), session)
    await update_product(product.id, ProductUpdate(watch_terms=["完全不同的商品"]), session)
    for _ in range(2):
        stats = await run_aggregation(session)
        await session.commit()
        assert stats.revisions_written == 0
        assert stats.events_deactivated == 0
        assert stats.evidences_removed == 0


async def test_reusing_previous_search_target_preserves_current_monitor_settings(session):
    from app.services.product_monitoring import configure_product_monitor

    product = Product(slug="monitor-audit", name="监控商品", watch_terms=["监控商品"])
    session.add(product)
    await session.flush()
    await configure_product_monitor(session, product, enabled=True, keyword="搜索词A")
    await session.commit()
    original_id = product.monitor_target_id
    await configure_product_monitor(session, product, keyword="搜索词B", interval_minutes=60, min_views=100)
    await session.commit()
    await configure_product_monitor(session, product, keyword="搜索词A")
    await session.commit()
    target = await session.get(CollectTarget, product.monitor_target_id)
    assert target.id == original_id
    assert target.enabled is True
    assert target.interval_minutes == 60 and target.min_views == 100
