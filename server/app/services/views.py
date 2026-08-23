"""事件/商品的 API 视图序列化（公开与管理端共用）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Event,
    EventRevision,
    Evidence,
    FULFILLMENT_STAGES,
    Product,
    RawContent,
    Source,
)
from app.schemas.api import (
    EventCorrectionOut,
    EventOut,
    EventSourceOut,
    ProductOut,
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
        .where(Evidence.event_id.in_(event_ids))
        .order_by(Evidence.event_id, RawContent.published_at.asc())
    )
    evidences_by_event: dict[int, list[EventSourceOut]] = {}
    conflicts_by_event: dict[int, list[EventSourceOut]] = {}
    for evidence, raw, source in evidence_rows:
        item = EventSourceOut(
            platform=raw.platform,
            account=(source.name if source else None),
            region=_REGION_OF_PLATFORM.get(raw.platform, "cn"),
            published_at=raw.published_at,
            url=raw.url,
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
                regions=(event.regions or []),
                evidence_count=len(sources) + len(conflicts),
                sources=sources,
                conflicts=conflicts if include_detail else [],
                corrections=corrections_by_event.get(event.id, []) if include_detail else [],
                first_seen_at=event.first_seen_at,
                updated_at=event.last_updated_at,
            )
        )
    return result


_STAGE_TONES = {1: "amber", 2: "sky", 3: "emerald", 4: "emerald", 5: "emerald"}
_STAGE_NOTES = {
    1: "仅证明实物样品存在，不代表消费者订单发货",
    2: "单一账号证据，需人工复核身份与商品对应关系",
    3: "多个独立账号，转载未重复计数",
    4: "等待相近购买时间段样本",
    5: "多购买时间段样本同时到货",
}


async def product_to_out(session: AsyncSession, product: Product) -> ProductOut:
    effective = (
        product.computed_stage if product.stage_hint < 0 else product.stage_hint
    )
    effective = max(0, min(effective, len(FULFILLMENT_STAGES) - 1))

    # 各阶段证据计数与最早日期（送测→阶段1；独立用户开箱→阶段2/3）
    rows = await session.execute(
        select(Evidence, RawContent, Source)
        .join(Event, Evidence.event_id == Event.id)
        .join(RawContent, Evidence.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(
            Event.product_id == product.id,
            Event.category.in_(["unboxing", "arrived"]),
        )
    )
    seeded_dates: list = []
    user_dates: list = []
    user_count = 0
    seeded_count = 0
    for _, raw, source in rows:
        published = raw.published_at
        identity = source.identity if source else "unknown"
        if identity == "seeded":
            seeded_count += 1
            if published:
                seeded_dates.append(published)
        elif identity not in ("official",):
            user_count += 1
            if published:
                user_dates.append(published)

    def _date(dates: list) -> str | None:
        return min(dates).strftime("%m-%d") if dates else None

    stages: list[ProductStageOut] = []
    for index, label in enumerate(FULFILLMENT_STAGES):
        state = "done" if index < effective else ("current" if index == effective else "pending")
        stage_evidence = None
        stage_date = None
        if index == 1:
            stage_evidence, stage_date = (seeded_count or None), _date(seeded_dates)
        elif index in (2, 3):
            stage_evidence = user_count or None
            stage_date = _date(user_dates)
        stages.append(
            ProductStageOut(
                label=label,
                state=state,
                tone=_STAGE_TONES.get(index),
                date=stage_date if state != "pending" else None,
                evidence=stage_evidence,
                note=_STAGE_NOTES.get(index),
            )
        )

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
        stage_hint=product.stage_hint,
        current_stage=effective,
        stage_label=FULFILLMENT_STAGES[effective],
        stages=stages,
        updated_at=product.updated_at,
    )
