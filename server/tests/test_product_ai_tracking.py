"""AI 商品追踪闭环：正文发现、多商品、重试、生命周期与公开呈现。"""

from datetime import timedelta
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import CollectTarget, ContentVersion, MerchScan, Product, ProductCandidate, ProductSignal, ProductSignalEvidence
from app.services import curation, product_discovery as discovery
from app.services.events import run_aggregation
from app.services.fulfillment import build_fulfillment_snapshot
from app.services.product_discovery_ai import parse_merch_response
from app.services.product_monitoring import configure_product_monitor
from app.services.queue import LocalRedis
from app.services.fulfillment_review_sweep import enqueue_pending_fulfillment_reviews
from app.services.settings import set_setting
from tests.test_curation_fulfillment_v2 import _response
from tests.test_fulfillment_automation import make_review
from tests.test_product_discovery import _official
from tests.test_parsers_bilibili import VIDEO_RAW
from app.services.ingest import ingest_items


def merch(name="菲比手办", *, quote=None, product_type="手办", **fields):
    return {"name": name, "product_type": product_type, "quote": quote or name,
            "confidence": 96, "facts": [], **fields}


def response(*items):
    return json.dumps({"products": list(items)}, ensure_ascii=False)


@pytest.fixture
def ai(monkeypatch):
    config = {"base_url": "https://example.com", "api_key": "test", "model": "test"}
    monkeypatch.setattr(discovery, "get_ai_config", AsyncMock(return_value=config))
    chat = AsyncMock(return_value=(response(), None))
    monkeypatch.setattr(discovery, "chat", chat)
    return chat


async def test_body_only_multiple_products_create_monitors_and_product_specific_facts(session, client, admin_headers, ai):
    await _official(session, title="周年周边上新", text="菲比手办开启预售，售价399元。安可耳机已经发货，售价599元。")
    ai.return_value = (response(
        merch(quote="菲比手办开启预售", price_note="399元", facts=[
            {"signal_type": "presale_open", "confidence": 96, "quote": "菲比手办开启预售"}]),
        merch("安可耳机", quote="安可耳机已经发货", product_type="耳机", price_note="599元", facts=[
            {"signal_type": "shipping_started", "confidence": 96, "quote": "安可耳机已经发货"}]),
    ), None)
    assert await discovery.discover_merch_candidates(session) == {"scanned": 1, "created": 2}
    products = list(await session.scalars(select(Product).order_by(Product.id)))
    assert [p.price_note for p in products] == ["399元", "599元"]
    targets = list(await session.scalars(select(CollectTarget).where(CollectTarget.id.in_([p.monitor_target_id for p in products]))))
    assert len(targets) == 2 and all(target.enabled for target in targets)
    assert not list(await session.scalars(select(ProductCandidate).where(ProductCandidate.status == "pending")))
    first = await build_fulfillment_snapshot(session, products[0].id)
    second = await build_fulfillment_snapshot(session, products[1].id)
    assert first["official"]["state"] == "presale_open"
    assert second["official"]["state"] == "shipping_started"
    for product, expected in zip(products, ("presale_open", "shipping_started")):
        signals = (await client.get(f"/api/products/{product.slug}/signals")).json()["items"]
        assert {signal["signal_type"] for signal in signals} == {expected}
        assert all(signal["review_method"] == "ai" for signal in signals)
    assert (await discovery.discover_merch_candidates(session))["scanned"] == 0
    assert ai.await_count == 1


async def test_ai_supports_new_goods_categories_and_merges_existing_id(session, ai):
    original = Product(slug="controller", name="菲比联名手柄", watch_terms=["菲比联名手柄"])
    session.add(original)
    await session.commit()
    await _official(session, title="联名周边发布", text="菲比联名游戏手柄正式公布。")
    ai.return_value = (response(merch("菲比联名游戏手柄", product_type="手柄", existing_product_id=original.id)), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 0
    assert len(list(await session.scalars(select(Product)))) == 1
    assert "菲比联名游戏手柄" in original.watch_terms


async def test_same_prefix_new_edition_is_not_merged_by_substring(session, ai):
    session.add(Product(slug="old", name="菲比手办", watch_terms=["菲比手办"]))
    await session.commit()
    await _official(session, title="菲比手办第二弹正式公布")
    ai.return_value = (response(merch("菲比手办第二弹")), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 1
    assert len(list(await session.scalars(select(Product)))) == 2


@pytest.mark.parametrize("name,quote", [("菲比手办", "菲比将以全新手办形象开启预售"), ("椿手办", "椿手办开启预售")])
async def test_ai_entity_link_survives_noncontiguous_names_and_short_character_names(session, ai, name, quote):
    await _official(session, title="周边上新", text=quote)
    ai.return_value = (response(merch(name, quote=quote, facts=[
        {"signal_type": "presale_open", "confidence": 96, "quote": quote}])), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 1
    product = await session.scalar(select(Product))
    assert (await build_fulfillment_snapshot(session, product.id))["official"]["state"] == "presale_open"


async def test_current_shipping_wins_over_historical_presale_in_the_same_announcement(session, ai):
    await _official(session, title="菲比手办履约更新", text="菲比手办八月开启预售，今天已经发货。")
    ai.return_value = (response(merch(facts=[
        {"signal_type": "presale_open", "confidence": 96, "quote": "菲比手办八月开启预售"},
        {"signal_type": "shipping_started", "confidence": 96, "quote": "今天已经发货"},
    ])), None)
    await discovery.discover_merch_candidates(session)
    product = await session.scalar(select(Product))
    assert (await build_fulfillment_snapshot(session, product.id))["official"]["state"] == "shipping_started"


async def test_one_uncertain_item_does_not_hold_back_other_clear_products(session, ai):
    now = utcnow()
    await _official(session, title="菲比手办与安可耳机发布")
    ai.return_value = (response(merch(), merch("安可耳机", product_type="耳机", confidence=40)), None)
    assert (await discovery.discover_merch_candidates(session, now=now))["created"] == 1
    ai.return_value = (response(merch(), merch("安可耳机", product_type="耳机")), None)
    assert (await discovery.discover_merch_candidates(session, now=now + timedelta(minutes=11)))["created"] == 1
    assert len(list(await session.scalars(select(Product)))) == 2


async def test_successful_empty_scan_is_revisited_after_a_new_content_version(session, ai):
    raw = await _official(session, title="周年周边预告")
    assert (await discovery.discover_merch_candidates(session))["scanned"] == 1
    assert (await discovery.discover_merch_candidates(session))["scanned"] == 0
    session.add(ContentVersion(raw_content_id=raw.id, version=2, title="周年周边预告", text="菲比手办开启预售。"))
    await session.commit()
    ai.return_value = (response(merch()), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 1
    assert (await session.get(MerchScan, raw.id)).content_version == 2


async def test_failure_cools_down_then_recovers_without_blocking_other_notices(session, ai):
    now = utcnow()
    first = await _official(session, title="菲比手办发布", external="first")
    await _official(session, title="安可耳机发布", external="second")
    ai.side_effect = [(response(merch("安可耳机", product_type="耳机")), None), ("bad json", None)]
    assert (await discovery.discover_merch_candidates(session, now=now))["created"] == 1
    assert (await session.get(MerchScan, first.id)).content_version == 0
    assert (await discovery.discover_merch_candidates(session, now=now + timedelta(minutes=1)))["scanned"] == 0
    ai.side_effect = None
    ai.return_value = (response(merch()), None)
    assert (await discovery.discover_merch_candidates(session, now=now + timedelta(minutes=11)))["created"] == 1


@pytest.mark.parametrize("fields", [
    {"quote": "原文没有出现的商品"},
    {"price_note": "999元"},
    {"confidence": 40},
    {"facts": [{"signal_type": "shipping_started", "confidence": 96, "quote": "已经全部发货"}]},
])
async def test_unsupported_ai_claims_do_not_publish_and_remain_retryable(session, ai, fields):
    raw = await _official(session, title="菲比手办发布", text="定价399元。")
    ai.return_value = (response(merch(**fields)), None)
    assert (await discovery.discover_merch_candidates(session))["created"] == 0
    assert not list(await session.scalars(select(Product)))
    assert (await session.get(MerchScan, raw.id)).content_version == 0


async def test_stale_ai_response_cannot_create_a_product_after_content_is_revised(session, ai):
    raw = await _official(session, title="菲比手办发布")
    async def revised(*args, **kwargs):
        session.add(ContentVersion(raw_content_id=raw.id, version=2, title="活动已取消"))
        await session.commit()
        return response(merch()), None
    ai.side_effect = revised
    assert (await discovery.discover_merch_candidates(session))["created"] == 0
    assert not list(await session.scalars(select(Product)))


def test_shipping_month_uses_the_end_of_the_window_and_rejects_invented_dates():
    published = utcnow().replace(year=2026, month=10, day=20)
    item = merch(expected_ship_at="2027-01-31T23:59:59+08:00", date_quote="预计2027年1月发货")
    parsed = parse_merch_response(response(item), title="菲比手办发布", text="预计2027年1月发货", published_at=published)
    assert parsed[0].expected_ship_at.month == 1
    item["expected_ship_at"] = "2027-02-28T23:59:59+08:00"
    with pytest.raises(ValueError):
        parse_merch_response(response(item), title="菲比手办发布", text="预计2027年1月发货", published_at=published)


async def test_presale_without_delivery_is_not_archived_just_because_it_is_quiet(session):
    product = Product(slug="future", name="菲比手办", watch_terms=["菲比手办"],
                      created_at=utcnow() - timedelta(days=100), original_ship_at="明年发货")
    session.add(product)
    await session.commit()
    assert (await discovery.archive_quiet_products(session))["archived"] == 0
    assert product.monitor_status == "watching"


@pytest.mark.parametrize("protection", ["future_shipping", "quality", "delayed", "resumed", None])
async def test_archive_only_after_delivery_and_the_entire_tracking_window_is_quiet(session, protection):
    now = utcnow()
    product = Product(slug="delivered", name="菲比手办", watch_terms=["菲比手办"],
                      created_at=now - timedelta(days=80), monitor_started_at=now - timedelta(days=80))
    session.add(product)
    await session.flush()
    await configure_product_monitor(session, product, enabled=True, keyword="菲比 手办")
    raw = await _official(session, title="菲比手办已经发货")
    signal = ProductSignal(product_id=product.id, signal_type="shipping_started", actor_type="official",
                           review_status="verified", summary="已发货", occurred_at=now - timedelta(days=40))
    session.add(signal)
    await session.flush()
    session.add(ProductSignalEvidence(signal_id=signal.id, raw_content_id=raw.id, independent_source_key="official"))
    if protection == "future_shipping":
        product.expected_ship_at = now + timedelta(days=90)
    elif protection == "resumed":
        product.monitor_started_at = now
    elif protection in {"quality", "delayed"}:
        extra = ProductSignal(product_id=product.id, signal_type="quality_issue" if protection == "quality" else "delayed",
                              actor_type="official", review_status="verified", summary=protection,
                              occurred_at=now - timedelta(days=1 if protection == "quality" else 31))
        session.add(extra)
        await session.flush()
        session.add(ProductSignalEvidence(signal_id=extra.id, raw_content_id=raw.id, independent_source_key="official"))
    await session.commit()
    assert (await discovery.archive_quiet_products(session, now=now))["archived"] == int(protection is None)


async def _archived_quality(session, *, published_at=None):
    product = Product(slug="quality", name="菲比手办", watch_terms=["菲比手办"],
                      created_at=utcnow() - timedelta(days=100))
    session.add(product)
    await session.flush()
    await configure_product_monitor(session, product, enabled=True, keyword="菲比 手办")
    await discovery.archive_product(session, product, now=utcnow() - timedelta(days=2))
    await session.commit()
    await set_setting(session, "ai", {"base_url": "https://example.com", "api_key": "test", "model": "test"})
    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, bvid="BVquality-new", title="菲比手办开箱，底座开裂",
                                                      description="菲比手办底座开裂。", cover_url=None)])
    from app.models import RawContent
    raw = await session.scalar(select(RawContent).where(RawContent.external_id == "BVquality-new"))
    raw.published_at = published_at or utcnow()
    await session.commit()
    await run_aggregation(session)
    await session.commit()
    review = make_review(product_ids=(product.id,), content_version=1, updates={
        (product.id, "quality_issue"): {"verdict": "supported", "confidence": 96,
                                        "quote": "菲比手办底座开裂。", "reason": "作者描述实物质量问题"}})
    return product, raw, review


async def test_archived_new_feedback_is_automatically_reviewed_and_restarts_tracking(session, monkeypatch):
    product, raw, review = await _archived_quality(session)
    enqueue = AsyncMock(return_value="queued")
    assert (await enqueue_pending_fulfillment_reviews(session, LocalRedis(), enqueue))["queued"] == 1
    monkeypatch.setattr(curation, "chat", AsyncMock(return_value=(_response(review), None)))
    assert (await curation.review_contents(session, [raw.id])).reviewed == 1
    snapshot = await build_fulfillment_snapshot(session, product.id)
    assert snapshot["quality"]["verified_count"] == 1
    assert product.monitor_status == "watching"
    assert product.archived_at is None
    assert (await session.get(CollectTarget, product.monitor_target_id)).enabled


async def test_historical_feedback_does_not_reactivate_an_archived_product(session, monkeypatch):
    product, raw, review = await _archived_quality(session, published_at=utcnow() - timedelta(days=10))
    monkeypatch.setattr(curation, "chat", AsyncMock(return_value=(_response(review), None)))
    assert (await curation.review_contents(session, [raw.id])).reviewed == 1
    assert (await build_fulfillment_snapshot(session, product.id))["quality"]["verified_count"] == 1
    assert product.monitor_status == "archived"
    assert not (await session.get(CollectTarget, product.monitor_target_id)).enabled


async def test_manually_reenabled_search_restores_monitor_status(session, client, admin_headers):
    product = Product(slug="resume", name="菲比手办", watch_terms=["菲比手办"])
    session.add(product)
    await session.flush()
    await configure_product_monitor(session, product, enabled=True, keyword="菲比 手办")
    await discovery.archive_product(session, product)
    await session.commit()
    restored = await client.put(f"/api/admin/products/{product.id}", headers=admin_headers, json={"monitor_enabled": True})
    assert restored.status_code == 200
    assert restored.json()["monitor_status"] == "watching"
    assert restored.json()["monitor"]["enabled"] is True
