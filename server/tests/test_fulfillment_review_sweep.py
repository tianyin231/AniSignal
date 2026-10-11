"""履约审核补偿：存量自动补齐、尊重人工与内容治理、成功不重复付费。"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from app.clock import utcnow
from app.models import AiUsage, ContentReview, ContentVersion, Product, ProductSignal, ProductSignalEvidence
from app.services import fulfillment_review_sweep as sweep
from app.services.fulfillment_automation import SIGNAL_TYPES
from app.services.queue import LocalRedis
from tests.test_notifications import _content


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(sweep, "get_ai_config", AsyncMock(return_value={
        "base_url": "https://test.invalid", "api_key": "test", "model": "test",
    }))


async def _candidate(session, suffix="1"):
    product = Product(slug=f"keyboard-{suffix}", name=f"键盘{suffix}", watch_terms=["键盘"], community="wuwa")
    session.add(product)
    await session.flush()
    raw = await _content(session, f"键盘自购到货 {suffix}", identity="user")
    signal = ProductSignal(product_id=product.id, community="wuwa", signal_type="consumer_received", summary="到货")
    session.add(signal)
    await session.flush()
    evidence = ProductSignalEvidence(signal_id=signal.id, raw_content_id=raw.id, independent_source_key=f"user-{suffix}")
    session.add(evidence)
    await session.commit()
    return product, signal, evidence, raw


def _complete_review(product_ids, version=1):
    return {"schema_version": 2, "content_version": version, "kind": "none", "confidence": 40,
            "reason": "证据不足", "signals": [
                {"product_id": product_id, "signal_type": kind, "verdict": "uncertain", "confidence": 40,
                 "quote": "", "reason": "证据不足", "acquisition_type": "unknown"}
                for product_id in product_ids for kind in SIGNAL_TYPES
            ]}


async def test_old_reviews_are_deduplicated_and_cooled_without_blocking_next_batch(session):
    first = await _candidate(session, "1")
    second = await _candidate(session, "2")
    session.add(ContentReview(raw_content_id=first[3].id, model="old", fulfillment={"kind": "self_purchase"}))
    # 同一原文多个轴/商品只入队一次。
    session.add(ProductSignalEvidence(signal_id=second[1].id, raw_content_id=first[3].id, independent_source_key="shared"))
    await session.commit()
    queue = AsyncMock(return_value="job")
    redis = LocalRedis()
    result = await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue, limit=1)
    assert result == {"candidates": 2, "queued": 1, "skipped_reason": None}
    assert queue.await_args.args == ([first[3].id],)
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue, limit=1))["queued"] == 1
    assert queue.await_args.args == ([second[3].id],)
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue))["queued"] == 0
    assert queue.await_count == 2


@pytest.mark.parametrize("invalid", ["locked", "inactive", "hidden", "excluded", "raw_community", "product_community"])
async def test_sweep_does_not_revive_governed_or_wrong_scope_evidence(session, invalid):
    product, signal, evidence, raw = await _candidate(session)
    if invalid == "locked":
        signal.locked = True
    elif invalid == "inactive":
        signal.active = False
    elif invalid == "hidden":
        raw.hidden = True
    elif invalid == "excluded":
        evidence.role = "excluded"
    elif invalid == "raw_community":
        raw.community = "hsr"
    else:
        product.community = "hsr"
    await session.commit()
    queue = AsyncMock(return_value="job")
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, LocalRedis(), queue))["candidates"] == 0
    queue.assert_not_awaited()


async def test_no_ai_configuration_is_observable_and_does_not_claim_retry(session, monkeypatch):
    await _candidate(session)
    queue = AsyncMock(return_value="job")
    redis = LocalRedis()
    monkeypatch.setattr(sweep, "get_ai_config", AsyncMock(return_value={}))
    assert await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue) == {
        "candidates": 0, "queued": 0, "skipped_reason": "ai_not_configured",
    }
    assert not redis._keys
    queue.assert_not_awaited()


async def test_failed_review_retries_after_cooldown_even_after_old_worker_retries(session):
    _, _, _, raw = await _candidate(session)
    now = utcnow()
    for _ in range(4):
        session.add(AiUsage(task="review_content", ref_id=raw.id, model="test", status="failed", created_at=now))
    await session.commit()
    queue = AsyncMock(return_value="job")
    redis = LocalRedis()
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue, now=now))["queued"] == 0
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue, now=now + timedelta(minutes=11)))["queued"] == 1


async def test_full_current_review_is_not_rebilled_but_new_version_is(session):
    product, _, _, raw = await _candidate(session)
    # uncertain 也是完成分析；不能因为仍在观察而每轮重新付费。
    complete = _complete_review([product.id])
    session.add(ContentReview(raw_content_id=raw.id, model="test", fulfillment=complete))
    await session.commit()
    queue = AsyncMock(return_value="job")
    redis = LocalRedis()
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue))["queued"] == 0
    queue.assert_not_awaited()
    session.add(ContentVersion(raw_content_id=raw.id, version=2, title="键盘收到后发现按键故障", category="quality_issue"))
    await session.commit()
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue))["queued"] == 1
    assert queue.await_args.args == ([raw.id],)


async def test_review_with_extra_locked_product_is_complete_but_new_product_requires_review(session):
    product, _, _, raw = await _candidate(session, "1")
    extra, extra_signal, _, _ = await _candidate(session, "2")
    extra_signal.locked = True
    session.add(ProductSignalEvidence(signal_id=extra_signal.id, raw_content_id=raw.id, independent_source_key="extra"))
    session.add(ContentReview(raw_content_id=raw.id, model="test", fulfillment=_complete_review([product.id, extra.id])))
    await session.commit()
    queue = AsyncMock(return_value="job")
    redis = LocalRedis()
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue))["queued"] == 0
    queue.assert_not_awaited()
    third, third_signal, _, third_raw = await _candidate(session, "3")
    third_raw.hidden = True
    session.add(ProductSignalEvidence(signal_id=third_signal.id, raw_content_id=raw.id, independent_source_key="third"))
    await session.commit()
    assert (await sweep.enqueue_pending_fulfillment_reviews(session, redis, queue))["queued"] == 1
    assert queue.await_args.args == ([raw.id],)
