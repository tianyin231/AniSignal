"""事件/商品的 API 视图序列化（公开与管理端共用）。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContentVersion,
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


_STAGE_TONES = {1: "amber", 2: "sky", 3: "emerald", 4: "emerald", 5: "emerald"}
_STAGE_NOTES = {
    1: "仅证明实物样品存在，不代表消费者订单发货",
    2: "单一账号证据，需人工复核身份与商品对应关系",
    3: "多个独立账号，转载未重复计数",
    4: "暂不可计算：尚未收集购买时间样本",
    5: "暂不可计算：尚未收集购买时间样本",
}

_OFFICIAL_STATUS = {
    "presale": "已发现官方预售信息",
    "balance": "已发现官方尾款信息",
    "delay": "已发现官方延期说明",
    "shipping": "已发现官方发货说明",
}


async def product_to_out(session: AsyncSession, product: Product) -> ProductOut:
    # 只使用有效事件与未排除证据；展示时重新从证据计算，避免历史阶段只升不降造成误导。
    rows = await session.execute(
        select(Evidence, Event, RawContent, Source)
        .join(Event, Evidence.event_id == Event.id)
        .join(RawContent, Evidence.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(
            Event.product_id == product.id,
            Event.is_active.is_(True),
            Evidence.role != "excluded",
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

    seeded_dates: list = []
    user_dates: list = []
    seeded_raw_ids: set[int] = set()
    user_sources: set[str] = set()
    evidence_items: list[ProductEvidenceOut] = []
    seen_raw_ids: set[int] = set()
    official_rows: list[tuple] = []
    for _, event, raw, source in evidence_rows:
        published = raw.published_at
        identity = source.identity if source else "unknown"
        if identity == "official" and event.category in _OFFICIAL_STATUS:
            official_rows.append((event, raw))
        if event.category in ("unboxing", "arrived") and identity == "seeded":
            seeded_raw_ids.add(raw.id)
            if published:
                seeded_dates.append(published)
        elif event.category in ("unboxing", "arrived") and identity == "user":
            user_sources.add(
                source.external_id if source else f"{raw.platform}:{raw.external_id}"
            )
            if published:
                user_dates.append(published)
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

    seeded_count = len(seeded_raw_ids)
    user_count = len(user_sources)
    observed_stage = 0
    if seeded_count:
        observed_stage = 1
    if user_count >= 1:
        observed_stage = 2
    if user_count >= 3:
        observed_stage = 3

    selected_ids = set(product.stage_hint_evidence_ids or [])
    hint_source = product.stage_hint_source or (
        "manual" if product.stage_hint >= 0 else None
    )
    manual_valid = bool(
        hint_source == "manual"
        and 0 < product.stage_hint <= 3
        and (product.stage_hint_reason or "").strip()
        and selected_ids
        and selected_ids.issubset(seen_raw_ids)
    )
    ai_valid = bool(
        hint_source == "ai"
        and 0 <= product.stage_hint <= 3
        and (product.stage_hint_reason or "").strip()
        and selected_ids.issubset(seen_raw_ids)
        and (product.stage_hint == 0 or selected_ids)
    )
    ai_applied = ai_valid and product.stage_hint >= observed_stage
    hint_applied = manual_valid or ai_applied
    effective = (
        product.stage_hint
        if manual_valid
        else max(observed_stage, product.stage_hint) if ai_valid else observed_stage
    )
    reviewed_sources = {
        source.external_id if source else f"{raw.platform}:{raw.external_id}"
        for _, event, raw, source in evidence_rows
        if hint_applied
        and product.stage_hint >= 2
        and raw.id in selected_ids
        and event.category in ("unboxing", "arrived")
        and (source.identity if source else "unknown") in ("user", "unknown")
    }
    stage_user_count = max(user_count, len(reviewed_sources))
    stage_source = (
        "manual"
        if manual_valid
        else "ai" if ai_applied else "automatic" if observed_stage else "unverified"
    )
    if manual_valid:
        stage_reason = product.stage_hint_reason.strip()
    elif ai_applied:
        stage_reason = product.stage_hint_reason.strip()
    elif observed_stage:
        stage_reason = f"已核验 {seeded_count} 条送测证据、{user_count} 个普通用户账号"
    else:
        stage_reason = "尚未发现可核验的送测或普通用户到货证据"

    if official_rows:
        official_event, _ = max(
            official_rows, key=lambda pair: pair[1].published_at or pair[1].collected_at
        )
        official_status = _OFFICIAL_STATUS[official_event.category]
    else:
        official_status = "未发现可核验的官方发货信息"
    if reviewed_sources:
        reviewer = "经 AI 审核" if ai_applied else "经人工审核"
        consumer_status = f"{reviewer}确认 {len(reviewed_sources)} 个独立账号的到货/开箱证据"
    elif user_count:
        consumer_status = f"已核验 {user_count} 个普通用户账号的到货/开箱证据"
    elif seeded_count:
        consumer_status = "仅发现送测样品，尚无普通用户到货证据"
    else:
        consumer_status = "暂无已核验的普通用户到货证据"

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
            stage_evidence = stage_user_count or None
            stage_date = _date(user_dates)
        note = _STAGE_NOTES.get(index)
        if hint_applied and index == effective:
            reviewer = "AI 审核" if ai_applied else "人工审核"
            note = (
                "AI 判断后未选定可确认到货的证据"
                if ai_applied and effective == 0
                else f"{reviewer}已绑定 {len(selected_ids)} 条原始内容证据"
            )
        stages.append(
            ProductStageOut(
                label=label,
                state=state,
                tone=_STAGE_TONES.get(index),
                date=stage_date if state != "pending" else None,
                evidence=stage_evidence,
                note=note,
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
        stage_source=stage_source,
        stage_reason=stage_reason,
        stage_evidence_ids=sorted(selected_ids) if hint_applied else [],
        stage_updated_at=(
            product.stage_hint_updated_at
            if hint_applied
            else max([*seeded_dates, *user_dates], default=None)
        ),
        official_status=official_status,
        official_evidence_count=len({raw.id for _, raw in official_rows}),
        consumer_status=consumer_status,
        consumer_evidence_count=stage_user_count,
        evidences=evidence_items,
        stages=stages,
        updated_at=product.updated_at,
    )
