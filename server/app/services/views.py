"""事件/商品的 API 视图序列化（公开与管理端共用）。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContentVersion,
    Event,
    EventRevision,
    Evidence,
    Product,
    RawContent,
    Source,
)
from app.schemas.api import (
    EventCorrectionOut,
    EventOut,
    EventSourceOut,
    ProductOut,
    ProductEvidenceOut,
    ProductStageOut,
)

_REGION_OF_PLATFORM = {
    "bilibili": "cn",
    "miyoushe": "cn",
    "kuro_cn": "cn",
    "kuro_global": "global",
    "youtube": "global",
}


async def events_to_out(
    session: AsyncSession, events: list[Event], include_detail: bool = False
) -> list[EventOut]:
    """批量序列化事件；include_detail 时附带 conflicts 与 corrections。"""
    if not events:
        return []
    event_ids = [e.id for e in events]

    evidence_rows = await session.execute(
        select(Evidence, RawContent, Source)
        .join(RawContent, Evidence.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(Evidence.event_id.in_(event_ids), RawContent.hidden.is_(False))
        .order_by(Evidence.event_id, RawContent.published_at.asc())
    )
    evidences_by_event: dict[int, list[EventSourceOut]] = {}
    conflicts_by_event: dict[int, list[EventSourceOut]] = {}
    keywords_by_event: dict[int, set] = {}
    latest_evidence_by_event: dict[int, datetime] = {}
    evidence_data = list(evidence_rows)
    raw_ids = [raw.id for _, raw, _ in evidence_data]
    titles: dict[int, str | None] = {}
    if raw_ids:
        version_rows = await session.execute(
            select(ContentVersion)
            .where(ContentVersion.raw_content_id.in_(raw_ids))
            .order_by(ContentVersion.raw_content_id, ContentVersion.version.desc())
        )
        for version in version_rows.scalars():
            titles.setdefault(version.raw_content_id, version.title)

    for evidence, raw, source in evidence_data:
        if evidence.role == "excluded":
            continue
        published = raw.published_at or raw.collected_at
        current_latest = latest_evidence_by_event.get(evidence.event_id)
        if current_latest is None or published > current_latest:
            latest_evidence_by_event[evidence.event_id] = published
        for kw in raw.matched_keywords or []:
            keywords_by_event.setdefault(evidence.event_id, set()).add(kw)
        item = EventSourceOut(
            platform=raw.platform,
            account=(source.name if source else None),
            title=titles.get(raw.id),
            region=_REGION_OF_PLATFORM.get(raw.platform, "cn"),
            published_at=raw.published_at,
            url=raw.url,
            content_id=raw.id,
        )
        bucket = (
            conflicts_by_event if evidence.role == "conflict" else evidences_by_event
        )
        bucket.setdefault(evidence.event_id, []).append(item)

    corrections_by_event: dict[int, list[EventCorrectionOut]] = {}
    if include_detail:
        revision_rows = await session.execute(
            select(EventRevision)
            .where(EventRevision.event_id.in_(event_ids))
            .order_by(EventRevision.event_id, EventRevision.id)
        )
        for revision in revision_rows.scalars():
            corrections_by_event.setdefault(revision.event_id, []).append(
                EventCorrectionOut(
                    at=revision.created_at, actor=revision.actor, note=revision.note
                )
            )

    products = {
        p.id: p
        for p in (
            await session.execute(
                select(Product).where(Product.id.in_([e.product_id for e in events if e.product_id]))
            )
        ).scalars()
    }

    result: list[EventOut] = []
    for event in events:
        sources = evidences_by_event.get(event.id, [])
        conflicts = conflicts_by_event.get(event.id, [])
        product = products.get(event.product_id) if event.product_id else None
        result.append(
            EventOut(
                id=event.id,
                title=event.title,
                summary=event.summary,
                summary_status=event.summary_status,
                summary_updated_at=event.summary_updated_at,
                category=event.category,
                product_id=event.product_id,
                product_slug=(product.slug if product else None),
                identity=event.identity,
                importance=event.importance,
                manual_locked=event.manual_locked,
                confidence=event.confidence,
                confidence_reason=event.confidence_reason,
                conclusion=event.conclusion,
                status_label=event.status_label,
                facts=(event.facts or []),
                matched_keywords=sorted(keywords_by_event.get(event.id, set()))[:6],
                regions=(event.regions or []),
                evidence_count=len(sources) + len(conflicts),
                sources=sources,
                conflicts=conflicts if include_detail else [],
                corrections=corrections_by_event.get(event.id, []) if include_detail else [],
                first_seen_at=event.first_seen_at,
                latest_evidence_at=latest_evidence_by_event.get(event.id),
                updated_at=event.last_updated_at,
            )
        )
    return result


async def product_to_out(session: AsyncSession, product: Product) -> ProductOut:
    """商品公开视图：结论只来自已核验履约信号，旧事件仅作证据目录。"""
    from app.services.fulfillment import build_fulfillment_snapshot, list_product_signals

    fulfillment = await build_fulfillment_snapshot(session, product.id)
    signals = await list_product_signals(session, product.id)

    rows = await session.execute(
        select(Evidence, Event, RawContent, Source)
        .join(Event, Evidence.event_id == Event.id)
        .join(RawContent, Evidence.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(
            Event.product_id == product.id,
            Event.is_active.is_(True),
            Evidence.role != "excluded",
            RawContent.hidden.is_(False),
        )
    )
    evidence_rows = list(rows)
    raw_ids = list({raw.id for _, _, raw, _ in evidence_rows})
    titles: dict[int, str | None] = {}
    if raw_ids:
        versions = await session.execute(
            select(ContentVersion)
            .where(ContentVersion.raw_content_id.in_(raw_ids))
            .order_by(ContentVersion.raw_content_id, ContentVersion.version.desc())
        )
        for version in versions.scalars():
            titles.setdefault(version.raw_content_id, version.title)

    evidence_items: list[ProductEvidenceOut] = []
    seen_raw_ids: set[int] = set()
    for _, event, raw, source in evidence_rows:
        identity = source.identity if source else "unknown"
        if raw.id not in seen_raw_ids:
            seen_raw_ids.add(raw.id)
            evidence_items.append(
                ProductEvidenceOut(
                    content_id=raw.id,
                    title=titles.get(raw.id) or raw.external_id,
                    platform=raw.platform,
                    account=(source.name if source else None),
                    identity=identity,
                    category=event.category,
                    published_at=raw.published_at,
                    url=raw.url,
                )
            )

    consumer = fulfillment["consumer"]
    verified_count = consumer["verified_count"]
    effective = 3 if verified_count >= 3 else 2 if verified_count else 1 if fulfillment["sample_count"] else 0
    verified_signal_ids = set(consumer["signal_ids"])
    selected_content_ids = sorted(
        {
            signal["content_id"]
            for signal in signals
            if signal["id"] in verified_signal_ids
        }
    )
    stages = [
        ProductStageOut(
            label="首位普通用户到货",
            state="done" if verified_count >= 3 else "current" if verified_count else "pending",
            tone="sky",
            evidence=verified_count or None,
            note="只统计经人工核验为自购订单的独立账号",
        ),
        ProductStageOut(
            label="多名独立用户到货",
            state="current" if verified_count >= 3 else "pending",
            tone="emerald",
            evidence=verified_count or None,
            note="至少三个独立自购账号；同账号内容不重复计数",
        ),
    ]

    return ProductOut(
        id=product.id,
        slug=product.slug,
        name=product.name,
        publisher=product.publisher,
        skus=(product.skus or []),
        price_note=product.price_note,
        presale_at=product.presale_at,
        balance_window=product.balance_window,
        original_ship_at=product.original_ship_at,
        ship_note=product.ship_note,
        watch_terms=(product.watch_terms or []),
        stage_hint=-1,
        current_stage=effective,
        stage_label=consumer["state_label"],
        stage_source="signals",
        stage_reason=consumer["summary"],
        stage_evidence_ids=selected_content_ids,
        stage_updated_at=consumer["updated_at"],
        official_status=fulfillment["official"]["summary"],
        official_evidence_count=fulfillment["official"]["verified_count"],
        consumer_status=consumer["summary"],
        consumer_evidence_count=verified_count,
        evidences=evidence_items,
        stages=stages,
        fulfillment=fulfillment,
        updated_at=product.updated_at,
    )
