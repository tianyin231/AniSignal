import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import AiUsage, ContentReview, ContentVersion, Product, RawContent
from app.services import curation, fulfillment
from app.services.ingest import ingest_items
from app.services.settings import set_setting
from tests.test_fulfillment_automation import make_review
from tests.test_parsers_bilibili import VIDEO_RAW


async def _seed_product_content(session):
    await set_setting(session, "ai", {"base_url": "https://example.com", "model": "mock", "api_key": "x"})
    product = Product(slug="aymiss", name="爱弥斯键盘", community="wuwa", watch_terms=["爱弥斯键盘"])
    session.add(product)
    await session.commit()
    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW,
        title="我自购的爱弥斯键盘今天到货开箱", description="本人开箱分享", cover_url=None)])
    return product, await session.scalar(select(RawContent))


def _response(review):
    return json.dumps({"review": {
        "relevant": True, "promotional": False, "violation": False, "quality_score": 80,
        "category_hint": "unboxing", "reason": "原文开箱", "links": [], "fulfillment": review,
    }}, ensure_ascii=False)


async def test_legacy_missing_axes_remain_retryable_then_valid_same_version_deduplicates(session, monkeypatch):
    product, raw = await _seed_product_content(session)
    old = {"kind": "self_purchase", "confidence": 60, "reason": "旧预判"}
    session.add(ContentReview(raw_content_id=raw.id, model="legacy", fulfillment=old, updated_at=utcnow()))
    await session.commit()
    chat = AsyncMock(return_value=(_response(old), None))
    monkeypatch.setattr(curation, "chat", chat)
    monkeypatch.setattr(curation, "_cover_data_url", AsyncMock(return_value=None))

    failed = await curation.review_contents(session, [raw.id])
    assert failed.candidates == failed.failed_batches == 1 and failed.reviewed == 0
    assert (await session.get(ContentReview, raw.id)).fulfillment == old
    assert (await session.scalar(select(AiUsage).order_by(AiUsage.id.desc()))).status == "failed"
    assert chat.await_count == 2  # 图片路径输出缺项后文本重试也缺项，不编造完成结果。

    valid = make_review(product_ids=(product.id,), content_version=999)
    chat.return_value = (_response(valid), None)
    succeeded = await curation.review_contents(session, [raw.id])
    assert succeeded.candidates == succeeded.reviewed == 1 and succeeded.failed_batches == 0
    stored = (await session.get(ContentReview, raw.id)).fulfillment
    assert stored["schema_version"] == 2 and stored["content_version"] == 1
    assert len(stored["signals"]) == 7
    assert (await curation.review_contents(session, [raw.id])).candidates == 0
    assert chat.await_count == 3


async def test_successful_review_refreshes_product_even_without_visibility_change(session, monkeypatch):
    product, raw = await _seed_product_content(session)
    observations = []

    async def capture(db, content_id):
        previous = await db.get(ContentReview, content_id)
        observations.append(("before", previous.fulfillment if previous else None))
        return {product.id: {"previous": True}}

    async def refresh(db, snapshots, *, reaggregate):
        current = await db.get(ContentReview, raw.id)
        observations.append(("after", current.fulfillment["content_version"]))
        assert snapshots == {product.id: {"previous": True}} and reaggregate is True
        assert raw.hidden is False

    monkeypatch.setattr(fulfillment, "capture_content_fulfillment", capture)
    monkeypatch.setattr(fulfillment, "refresh_content_fulfillment", refresh)
    monkeypatch.setattr(curation, "chat", AsyncMock(return_value=(_response(make_review(product_ids=(product.id,))), None)))
    result = await curation.review_contents(session, [raw.id])
    assert result.reviewed == 1
    assert observations == [("before", None), ("after", 1)]


async def test_product_context_keeps_every_matching_product_and_requires_every_axis(session, monkeypatch):
    first, raw = await _seed_product_content(session)
    second = Product(slug="dania", name="达妮娅键盘", community="wuwa", watch_terms=["达妮娅键盘"])
    session.add(second)
    await session.commit()
    version = await session.scalar(select(ContentVersion))
    version.text = "爱弥斯键盘比较值。达妮娅键盘质量一般。"
    await session.commit()
    seen = []

    async def chat(config, system, user, *, json_mode=False):
        payload = json.loads(user if isinstance(user, str) else user[0]["text"])
        seen.append(payload["product_context"])
        assert "不能将另一商品评价归给标题商品" in system
        return _response(make_review(product_ids=(first.id, second.id))), None

    monkeypatch.setattr(curation, "chat", chat)
    assert (await curation.review_contents(session, [raw.id])).reviewed == 1
    assert {item["id"] for item in seen[0]["products"]} == {first.id, second.id}
    stored = (await session.get(ContentReview, raw.id)).fulfillment
    assert len(stored["signals"]) == 14


@pytest.mark.parametrize("quote", ["模型编造的收货原文", "我自购的爱弥斯键盘今天到货开箱 本人开箱分享"])
async def test_ungrounded_and_cross_field_quote_stay_retryable(session, monkeypatch, quote):
    product, raw = await _seed_product_content(session)
    review = make_review(product_ids=(product.id,), updates={(product.id, "consumer_received"): {
        "verdict": "supported", "confidence": 99, "quote": quote, "reason": "声称收到",
    }})
    chat = AsyncMock(return_value=(_response(review), None))
    monkeypatch.setattr(curation, "chat", chat)
    result = await curation.review_contents(session, [raw.id])
    assert result.failed_batches == 1 and result.reviewed == 0
    assert await session.get(ContentReview, raw.id) is None


async def test_text_retry_explains_quote_failure_and_accepts_corrected_v2(session, monkeypatch):
    product, raw = await _seed_product_content(session)
    calls = []

    async def chat(config, system, user, *, json_mode=False):
        calls.append(system)
        payload = json.loads(user if isinstance(user, str) else user[0]["text"])
        if len(calls) == 1:
            quote = f"{payload['title']} {payload['text']}"
            assert "上轮未通过 schema/引文校验" not in system
        else:
            assert "上轮未通过 schema/引文校验" in system
            assert '错误 quote="某键盘到货开箱，补款后收到"' in system
            assert "不能推理官方已发货" in system
            assert "不因此降低实物到手轴" in system
            quote = payload["title"]
        review = make_review(product_ids=(product.id,), updates={(product.id, "consumer_received"): {
            "verdict": "supported", "confidence": 95, "quote": quote, "reason": "明确实物开箱", "acquisition_type": "unknown",
        }})
        return _response(review), None

    monkeypatch.setattr(curation, "chat", chat)
    result = await curation.review_contents(session, [raw.id])
    assert result.reviewed == 1 and result.failed_batches == 0 and len(calls) == 2
    stored = (await session.get(ContentReview, raw.id)).fulfillment
    assert stored["schema_version"] == 2 and stored["content_version"] == 1
    receipt = next(item for item in stored["signals"] if item["signal_type"] == "consumer_received")
    assert receipt["quote"] == "我自购的爱弥斯键盘今天到货开箱"
