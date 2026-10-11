"""自动履约从可追溯判定到公开状态、纠错与通知修订的回归。"""

from sqlalchemy import func, select
import pytest

from app.models import (ContentReview, ContentVersion, Event, Evidence, Product,
                        ProductFulfillmentRevision, ProductSignal, ProductSignalRevision,
                        RawContent, Source)
from app.services.fulfillment import (build_fulfillment_snapshot, list_product_signals,
                                      review_product_signal, sync_product_signals)
from app.services.fulfillment_automation import SIGNAL_TYPES


def _assessment(product_id, *, version=1, acquisition="unknown", verdict="supported", quote="测试键盘开箱", quality=False):
    return {
        "schema_version": 2, "content_version": version, "kind": "none", "confidence": 90,
        "reason": "逐轴判断", "signals": [{
            "product_id": product_id, "signal_type": kind,
            "verdict": verdict if kind == "consumer_received" or (quality and kind == "quality_issue") else "uncertain",
            "confidence": 95, "quote": quote, "reason": "原文提供了对应证据",
            "acquisition_type": acquisition,
        } for kind in SIGNAL_TYPES],
    }


async def _prepare(session, *, text="测试键盘开箱", quality=False):
    product = Product(slug="auto-keyboard", name="测试键盘", watch_terms=["测试键盘"])
    source = Source(platform="bilibili", external_id="real-person", name="买家", identity="user", raw_json={})
    session.add_all([product, source])
    await session.flush()
    raw = RawContent(platform="bilibili", content_type="video", external_id="auto-video", source_id=source.id, raw_json={})
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, title="测试键盘开箱", text=text, category="unboxing"))
    for category in (["unboxing", "quality"] if quality else ["unboxing"]):
        event = Event(product_id=product.id, category=category, title="测试键盘", community="wuwa", is_active=True)
        session.add(event)
        await session.flush()
        session.add(Evidence(event_id=event.id, raw_content_id=raw.id))
    await session.flush()
    await sync_product_signals(session, product.id)
    return product, raw


async def _save_review(session, product, raw, **kwargs):
    review = await session.get(ContentReview, raw.id)
    if review is None:
        review = ContentReview(raw_content_id=raw.id, model="test", relevant=True, promotional=False, violation=False, quality_score=90)
        session.add(review)
    review.fulfillment = _assessment(product.id, **kwargs)
    await session.flush()
    await sync_product_signals(session, product.id)
    return review


async def test_automatic_observation_is_useful_without_claiming_self_purchase(session):
    product, raw = await _prepare(session)
    await _save_review(session, product, raw)
    state = await build_fulfillment_snapshot(session, product.id)
    assert state["consumer"]["state"] == "observed_receipts"
    assert state["consumer"]["verified_count"] == 0
    assert state["observation_count"] == state["automated_count"] == 1
    signals = await list_product_signals(session, product.id)
    assert signals[0]["review_method"] == "ai"
    assert signals[0]["claim_quote"] == "测试键盘开箱"
    assert await session.scalar(select(func.count(ProductFulfillmentRevision.id))) == 1
    revisions = await session.scalar(select(func.count(ProductSignalRevision.id)))
    stamp = state["updated_at"]
    await sync_product_signals(session, product.id)
    assert (await build_fulfillment_snapshot(session, product.id))["updated_at"] == stamp
    assert await session.scalar(select(func.count(ProductSignalRevision.id))) == revisions


async def test_automatic_self_purchase_updates_public_state_and_retracts_hidden_evidence(session):
    quote = "我自购的测试键盘已到货，今天开箱"
    product, raw = await _prepare(session, text=quote)
    await _save_review(session, product, raw, acquisition="self_purchase", quote=quote)
    state = await build_fulfillment_snapshot(session, product.id)
    assert state["consumer"]["verified_count"] == state["automated_count"] == 1
    assert state["manual_count"] == 0
    raw.hidden = True
    await session.flush()
    await sync_product_signals(session, product.id, before=state)
    assert (await build_fulfillment_snapshot(session, product.id))["consumer"]["verified_count"] == 0
    revision = await session.scalar(select(ProductFulfillmentRevision).order_by(ProductFulfillmentRevision.id.desc()))
    assert revision.previous_snapshot["consumer"]["verified_count"] == 1
    assert revision.snapshot["consumer"]["verified_count"] == 0


async def test_receipt_assessment_does_not_certify_quality_axis(session):
    quote = "我自购的测试键盘开箱，发现品控问题"
    product, raw = await _prepare(session, text=quote, quality=True)
    await _save_review(session, product, raw, acquisition="self_purchase", quote=quote)
    state = await build_fulfillment_snapshot(session, product.id)
    assert state["consumer"]["verified_count"] == 1
    assert state["quality"]["verified_count"] == 0
    assert state["quality"]["candidate_count"] == 1
    await _save_review(session, product, raw, acquisition="self_purchase", quote=quote, quality=True)
    assert (await build_fulfillment_snapshot(session, product.id))["quality"]["verified_count"] == 1


async def test_manual_correction_is_not_overwritten_by_automatic_reprocessing(session):
    product, raw = await _prepare(session)
    await _save_review(session, product, raw)
    signal = await session.scalar(select(ProductSignal))
    await review_product_signal(session, product.id, signal.id, review_status="rejected", actor_type=None,
                                acquisition_type=None, summary=None, claim_quote=None, note="管理员发现转载")
    await sync_product_signals(session, product.id)
    signals = await list_product_signals(session, product.id, include_rejected=True)
    assert signals[0]["review_status"] == "rejected"
    assert signals[0]["review_method"] == "manual"
    assert (await build_fulfillment_snapshot(session, product.id))["observation_count"] == 0


async def test_changed_original_cannot_reuse_old_ai_approval(session):
    product, raw = await _prepare(session)
    await _save_review(session, product, raw)
    session.add(ContentVersion(raw_content_id=raw.id, version=2, title="测试键盘开箱", text="尚未收到，只是转发", category="unboxing"))
    await session.flush()
    await sync_product_signals(session, product.id)
    state = await build_fulfillment_snapshot(session, product.id)
    assert state["observation_count"] == 0
    assert state["candidate_total"] == 1
    await _save_review(session, product, raw, version=2, verdict="unsupported", quote="尚未收到，只是转发")
    signal = await session.scalar(select(ProductSignal))
    assert signal.review_status == "rejected"
    assert (await build_fulfillment_snapshot(session, product.id))["candidate_total"] == 0


async def test_review_completion_immediately_refreshes_signal_without_visibility_change(session, monkeypatch):
    from app.services import curation
    from app.services.settings import set_setting

    product, raw = await _prepare(session)
    await set_setting(session, "ai", {"base_url": "https://example.com", "model": "test", "api_key": "x"})

    async def review_one(config, candidate):
        return {"relevant": True, "promotional": False, "violation": False, "quality_score": 90,
                "category_hint": "unboxing", "reason": "原文开箱", "links": [],
                "fulfillment": _assessment(product.id)}, None, False, 1

    monkeypatch.setattr(curation, "_review_one", review_one)
    stats = await curation.review_contents(session, [raw.id])
    assert stats.reviewed == 1
    assert (await build_fulfillment_snapshot(session, product.id))["observation_count"] == 1


async def test_ai_can_add_a_grounded_axis_missed_by_keyword_rules(session):
    from app.services.events import run_aggregation

    quote = "测试键盘的空格按下去完全没有反应"
    product, raw = await _prepare(session, text=quote)
    await _save_review(session, product, raw, quote=quote, quality=True)
    await run_aggregation(session)
    assert (await build_fulfillment_snapshot(session, product.id))["quality"]["verified_count"] == 1


async def test_two_matching_products_do_not_share_an_ai_verdict(session):
    product, raw = await _prepare(session)
    other = Product(slug="another-product", name="另一款测试键盘", watch_terms=["测试键盘"])
    session.add(other)
    await session.flush()
    event = Event(product_id=other.id, category="unboxing", title="测试键盘", community="wuwa", is_active=True)
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id))
    await _save_review(session, product, raw)
    await sync_product_signals(session, other.id)
    assert (await build_fulfillment_snapshot(session, product.id))["observation_count"] == 1
    assert (await build_fulfillment_snapshot(session, other.id))["observation_count"] == 0


@pytest.mark.parametrize("transition", ["reparse", "hide_restore"])
async def test_disabled_event_cannot_revive_through_category_drift_or_visibility(session, transition):
    from app.services.events import run_aggregation

    product, raw = await _prepare(session)
    review = await _save_review(session, product, raw)
    event = await session.scalar(select(Event))
    event.is_active = False
    event.manual_locked = True
    await session.flush()
    await sync_product_signals(session, product.id)
    if transition == "reparse":
        session.add(ContentVersion(raw_content_id=raw.id, version=2, title="测试键盘开箱", text="测试键盘开箱", category=None))
        review.fulfillment = _assessment(product.id, version=2)
    else:
        raw.hidden = True
        await session.flush()
        await run_aggregation(session)
        raw.hidden = False
    await session.flush()
    await run_aggregation(session)
    assert (await build_fulfillment_snapshot(session, product.id))["observation_count"] == 0
    assert await session.scalar(select(func.count(Event.id)).where(Event.product_id == product.id, Event.is_active.is_(True))) == 0
    assert await session.scalar(select(func.count(Evidence.id)).where(Evidence.event_id == event.id)) == 1
