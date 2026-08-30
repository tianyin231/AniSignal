"""结构化履约信号的计数与降噪规则。"""

from app.models import (
    Product,
    ProductSignal,
    ProductSignalEvidence,
    RawContent,
    Source,
)
from app.services.fulfillment import build_fulfillment_snapshot


async def _raw(session, external_id: str, source: Source) -> RawContent:
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id=external_id,
        source_id=source.id,
        url=f"https://example.com/{external_id}",
        raw_json={},
    )
    session.add(raw)
    await session.flush()
    return raw


async def _signal(
    session,
    product_id: int,
    raw: RawContent,
    signal_type: str,
    source_key: str,
    acquisition_type: str = "unknown",
    review_status: str = "verified",
) -> ProductSignal:
    signal = ProductSignal(
        product_id=product_id,
        signal_type=signal_type,
        actor_type="user",
        review_status=review_status,
        summary=raw.external_id,
        details={},
        source_method="rule",
        active=True,
        locked=True,
    )
    session.add(signal)
    await session.flush()
    session.add(
        ProductSignalEvidence(
            signal_id=signal.id,
            raw_content_id=raw.id,
            claim_quote=signal.summary,
            acquisition_type=acquisition_type,
            independent_source_key=source_key,
        )
    )
    return signal


async def test_snapshot_deduplicates_sources_and_separates_seeded_samples(session):
    product = Product(slug="signals", name="信号测试商品", watch_terms=["信号测试"])
    session.add(product)
    await session.flush()
    sources = []
    for external_id, identity in [
        ("user-1", "user"),
        ("seeded-1", "seeded"),
        ("quality-1", "user"),
        ("quality-2", "user"),
        ("candidate-1", "unknown"),
    ]:
        source = Source(
            platform="bilibili",
            external_id=external_id,
            name=external_id,
            identity=identity,
            raw_json={},
        )
        session.add(source)
        sources.append(source)
    await session.flush()

    receipt_a = await _raw(session, "receipt-a", sources[0])
    receipt_b = await _raw(session, "receipt-b", sources[0])
    seeded = await _raw(session, "seeded", sources[1])
    quality_a = await _raw(session, "quality-a", sources[2])
    quality_b = await _raw(session, "quality-b", sources[3])
    candidate = await _raw(session, "candidate", sources[4])
    await _signal(
        session, product.id, receipt_a, "consumer_received", "bilibili:user-1", "self_purchase"
    )
    await _signal(
        session, product.id, receipt_b, "consumer_received", "bilibili:user-1", "self_purchase"
    )
    await _signal(
        session, product.id, seeded, "consumer_received", "bilibili:seeded-1", "seeded"
    )
    await _signal(session, product.id, quality_a, "quality_issue", "bilibili:quality-1")
    await _signal(session, product.id, quality_b, "quality_issue", "bilibili:quality-2")
    await _signal(
        session,
        product.id,
        candidate,
        "consumer_received",
        "bilibili:candidate-1",
        review_status="candidate",
    )
    await session.flush()

    snapshot = await build_fulfillment_snapshot(session, product.id)
    assert snapshot["consumer"]["verified_count"] == 1
    assert snapshot["consumer"]["state"] == "first_receipt"
    assert snapshot["consumer"]["candidate_count"] == 1
    assert snapshot["sample_count"] == 1
    assert snapshot["quality"]["state"] == "repeated_issue"
    assert snapshot["quality"]["verified_count"] == 2

    receipt_a.hidden = True
    receipt_b.hidden = True
    await session.flush()
    snapshot = await build_fulfillment_snapshot(session, product.id)
    assert snapshot["consumer"]["verified_count"] == 0
    assert snapshot["consumer"]["state"] == "no_verified_receipt"
