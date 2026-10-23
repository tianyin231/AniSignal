"""事件/商品的 API 视图序列化（公开与管理端共用）。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.communities import DEFAULT_COMMUNITY
from app.models import (
    CollectTarget,
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


async def _latest_titles(session: AsyncSession, raw_ids: list[int]) -> dict[int, str | None]:
    if not raw_ids:
        return {}
    latest = (
        select(ContentVersion.raw_content_id.label("rid"), func.max(ContentVersion.version).label("v"))
        .where(ContentVersion.raw_content_id.in_(raw_ids))
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    rows = await session.execute(
        select(ContentVersion.raw_content_id, ContentVersion.title).join(
            latest, (ContentVersion.raw_content_id == latest.c.rid) & (ContentVersion.version == latest.c.v)
        )
    )
    return dict(rows.all())


async def events_to_out(
    session: AsyncSession, events: list[Event], include_detail: bool = False
) -> list[EventOut]:
    """批量序列化事件；include_detail 时附带 conflicts 与 corrections。"""
    if not events:
        return []
    event_ids = [e.id for e in events]

    evidence_rows = await session.execute(
        select(Evidence, RawContent, Source)
        .options(defer(RawContent.raw_json))
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
    titles = await _latest_titles(session, raw_ids)

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
            identity=(source.identity if source else "unknown"),
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
                community=(event.community or DEFAULT_COMMUNITY),
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


async def product_to_out(session: AsyncSession, product: Product, *, include_pending: bool = False) -> ProductOut:
    """商品公开视图：结论只来自已核验履约信号，旧事件仅作证据目录。"""
    from app.services.fulfillment import build_fulfillment_snapshot, list_product_signals, load_product_signal_data
    from app.services.product_discovery import public_brief

    signal_data = await load_product_signal_data(session, product.id)
    if not include_pending:
        signal_data = ([row for row in signal_data[0] if row[0].review_status == "verified"], {})
    fulfillment = await build_fulfillment_snapshot(session, product.id, signal_data=signal_data)
    signals = await list_product_signals(session, product.id, signal_data=signal_data, include_notes=False)

    rows = await session.execute(
        select(Evidence, Event, RawContent, Source)
        .options(defer(RawContent.raw_json))
        .join(Event, Evidence.event_id == Event.id)
        .join(RawContent, Evidence.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(
            Event.product_id == product.id,
            Event.community == product.community,
            Event.is_active.is_(True),
            Evidence.role != "excluded",
            RawContent.hidden.is_(False),
            RawContent.community == product.community,
        )
    )
    evidence_rows = list(rows)
    raw_ids = list({raw.id for _, _, raw, _ in evidence_rows})
    titles = await _latest_titles(session, raw_ids)

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

    latest_candidate_signal = next(
        (s for s in signals if s["review_status"] == "candidate"), None
    )
    latest_candidate = (
        {
            "summary": latest_candidate_signal["summary"],
            "signal_label": latest_candidate_signal["signal_label"],
            "occurred_at": latest_candidate_signal["occurred_at"],
            "content_id": latest_candidate_signal["content_id"],
        }
        if latest_candidate_signal
        else None
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
            note="只统计有明确自购到货证据的独立账号；自动判断可追溯、可纠正",
        ),
        ProductStageOut(
            label="多名独立用户到货",
            state="current" if verified_count >= 3 else "pending",
            tone="emerald",
            evidence=verified_count or None,
            note="至少三个独立自购账号；同账号内容不重复计数",
        ),
    ]

    target = await session.get(CollectTarget, product.monitor_target_id) if product.monitor_target_id else None
    monitor = {
        "target_id": target.id, "keyword": target.target_value, "enabled": target.enabled,
        "interval_minutes": target.interval_minutes, "min_views": target.min_views, "tier": target.tier,
    } if target else None
    return ProductOut(
        id=product.id,
        slug=product.slug,
        name=product.name,
        product_type=(product.discovery or {}).get("product_type"),
        images=(product.discovery or {}).get("images") or [],
        images_source_url=(product.discovery or {}).get("images_source_url"),
        publisher=product.publisher,
        community=(product.community or DEFAULT_COMMUNITY),
        skus=(product.skus or []),
        price_note=product.price_note,
        presale_at=product.presale_at,
        balance_window=product.balance_window,
        original_ship_at=product.original_ship_at,
        ship_note=product.ship_note,
        watch_terms=(product.watch_terms or []),
        monitor_status=product.monitor_status or "watching",
        source_url=product.source_url,
        brief=public_brief(product, fulfillment, signals),
        monitor=monitor,
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
        latest_candidate=latest_candidate,
        updated_at=product.updated_at,
    )
