"""结构化商品履约信号：规则同步、人工审核与三轴状态投影。"""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.clock import utcnow
from app.communities import DEFAULT_COMMUNITY
from app.services.product_matching import detect_product_signal_types, matches_product_text
from app.models import (
    ContentReview,
    ContentVersion,
    Event,
    Evidence,
    Product,
    ProductSignal,
    ProductSignalEvidence,
    ProductSignalRevision,
    RawContent,
    Source,
)

CATEGORY_TO_SIGNAL = {
    "presale": "presale_open",
    "balance": "balance_open",
    "delay": "delayed",
    "shipping": "shipping_started",
    "arrived": "consumer_received",
    "unboxing": "consumer_received",
    "quality": "quality_issue",
}

SIGNAL_LABELS = {
    "presale_open": "开启预售",
    "balance_open": "开启尾款",
    "delayed": "延期",
    "shipping_started": "开始发货",
    "aftersales_notice": "售后说明",
    "consumer_received": "消费者到货",
    "quality_issue": "质量问题",
}

OFFICIAL_TYPES = {
    "presale_open",
    "balance_open",
    "delayed",
    "shipping_started",
    "aftersales_notice",
}
REVIEW_STATUSES = {"candidate", "verified", "rejected", "conflict"}
ACQUISITION_TYPES = {"unknown", "self_purchase", "seeded"}
ACTOR_TYPES = {"official", "user", "seeded", "media", "merchant", "unknown"}


def _plain(value: str | None) -> str:
    import bleach

    return re.sub(r"\s+", " ", bleach.clean(value or "", tags=[], strip=True)).strip()


def _source_key(raw: RawContent, source: Source | None) -> str:
    if source and source.external_id:
        return f"{raw.platform}:{source.external_id}"
    return f"{raw.platform}:content:{raw.external_id}"


async def sync_product_signals(
    session: AsyncSession, product_id: int, *, before: dict | None = None,
) -> int:
    """对账当前有效证据，保留人工审核字段，失效证据退出公开计算。"""
    product = await session.scalar(select(Product).where(Product.id == product_id).with_for_update().execution_options(populate_existing=True))
    if product is None:
        return 0
    if before is None:
        before = await build_fulfillment_snapshot(session, product_id)
    product_community = product.community or DEFAULT_COMMUNITY
    latest = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    rows = list(
        await session.execute(
            select(Event, RawContent, Source, ContentVersion)
            .options(defer(RawContent.raw_json))
            .join(Evidence, Evidence.event_id == Event.id)
            .join(RawContent, RawContent.id == Evidence.raw_content_id)
            .join(Source, Source.id == RawContent.source_id, isouter=True)
            .join(latest, latest.c.rid == RawContent.id)
            .join(
                ContentVersion,
                (ContentVersion.raw_content_id == latest.c.rid)
                & (ContentVersion.version == latest.c.version),
            )
            .where(
                Event.product_id == product_id,
                Event.community == product_community,
                Event.is_active.is_(True),
                Evidence.role == "support",
                RawContent.community == product_community,
                RawContent.hidden.is_(False),
                ~select(Evidence.id).where(
                    Evidence.raw_content_id == RawContent.id, Evidence.role == "excluded",
                ).correlate(RawContent).exists(),
            )
            .execution_options(populate_existing=True)
        )
    )

    existing_rows = list(
        await session.execute(
            select(ProductSignal, ProductSignalEvidence)
            .join(ProductSignalEvidence, ProductSignalEvidence.signal_id == ProductSignal.id)
            .where(ProductSignal.product_id == product_id)
        )
    )
    existing = {
        (evidence.raw_content_id, signal.signal_type): (signal, evidence)
        for signal, evidence in existing_rows
    }
    expected = {}
    event_signal_types = {
        category: {kind} for category, kind in CATEGORY_TO_SIGNAL.items()
    }
    event_signal_types["quality"].add("aftersales_notice")
    for event, raw, source, version in rows:
        if not matches_product_text(version.title, version.text, product.watch_terms or []):
            continue
        for kind in detect_product_signal_types(version.title, version.text, version.category) & event_signal_types.get(event.category, set()):
            expected[(raw.id, kind)] = (event, raw, source, version)
    changed = False
    for signal, evidence in existing_rows:
        if signal.source_method == "rule":
            active = (evidence.raw_content_id, signal.signal_type) in expected and signal.community == product_community
            if signal.active != active:
                signal.active = active
                changed = True

    created = 0
    for key, (event, raw, source, version) in expected.items():
        signal_type = key[1]
        actor = source.identity if source else "unknown"
        is_official = actor == "official" and signal_type in OFFICIAL_TYPES
        title = _plain(version.title or event.title or raw.external_id)
        quote = _plain(version.text or version.title or event.title)[:500] or title
        current = existing.get(key)
        if current:
            signal, evidence = current
            # 不把其他分区历史信号换标签复活。
            if signal.community != product_community:
                continue
            signal.active = True
            if not signal.locked:
                signal.actor_type = actor
                signal.occurred_at = raw.published_at or raw.collected_at
                signal.summary = title
                signal.confidence = 1.0 if is_official else 0.5
                signal.review_status = "verified" if is_official else "candidate"
                evidence.claim_quote = quote
                evidence.independent_source_key = _source_key(raw, source)
                evidence.acquisition_type = "seeded" if actor == "seeded" else "unknown"
            changed = changed or session.is_modified(signal, include_collections=False) or session.is_modified(evidence, include_collections=False)
            continue

        signal = ProductSignal(
            product_id=product_id,
            community=product_community,
            signal_type=signal_type,
            actor_type=actor,
            review_status="verified" if is_official else "candidate",
            occurred_at=raw.published_at or raw.collected_at,
            summary=title,
            confidence=1.0 if is_official else 0.5,
            details={"legacy_event_id": event.id, "legacy_category": event.category},
            source_method="rule",
            active=True,
        )
        session.add(signal)
        await session.flush()
        evidence = ProductSignalEvidence(
            signal_id=signal.id,
            raw_content_id=raw.id,
            claim_quote=quote,
            acquisition_type="seeded" if actor == "seeded" else "unknown",
            independent_source_key=_source_key(raw, source),
        )
        session.add(evidence)
        existing[key] = (signal, evidence)
        created += 1
    if changed or created:
        product.fulfillment_updated_at = utcnow()
    await session.flush()
    from app.services.fulfillment_revisions import record_product_fulfillment_change

    after = await build_fulfillment_snapshot(session, product_id)
    if before != after and not (changed or created):
        product.fulfillment_updated_at = utcnow()
        after["updated_at"] = product.fulfillment_updated_at
    await record_product_fulfillment_change(session, product_id, before, after, "履约证据同步")
    return created


async def _active_signal_rows(session: AsyncSession, product_id: int) -> list[tuple]:
    return list(
        await session.execute(
            select(ProductSignal, ProductSignalEvidence, RawContent, Source)
            .options(defer(RawContent.raw_json))
            .join(ProductSignalEvidence, ProductSignalEvidence.signal_id == ProductSignal.id)
            .join(RawContent, RawContent.id == ProductSignalEvidence.raw_content_id)
            .join(Product, Product.id == ProductSignal.product_id)
            .join(Source, Source.id == RawContent.source_id, isouter=True)
            .where(
                ProductSignal.product_id == product_id,
                ProductSignal.active.is_(True),
                ProductSignal.community == Product.community,
                RawContent.community == Product.community,
                RawContent.hidden.is_(False),
                ProductSignalEvidence.role != "excluded",
            )
            .order_by(ProductSignal.occurred_at.desc(), ProductSignal.id.desc())
            .execution_options(populate_existing=True)
        )
    )


async def load_product_signal_data(session: AsyncSession, product_id: int) -> tuple[list, dict]:
    """同一次商品响应共享信号与 AI 提示，避免三轴快照和候选列表各查一遍。"""
    rows = await _active_signal_rows(session, product_id)
    raw_ids = {e.raw_content_id for _, e, _, _ in rows}
    hints = {}
    if raw_ids:
        result = await session.execute(
            select(ContentReview.raw_content_id, ContentReview.fulfillment).where(
                ContentReview.raw_content_id.in_(raw_ids),
                ContentReview.fulfillment.is_not(None),
            )
        )
        hints = dict(result.all())
    return rows, hints


KIND_PREVIEW_LABELS = {
    "self_purchase": "自购开箱",
    "pr_sample": "送测样品",
    "forward": "转发剪辑",
}


def _ai_preview(
    candidates: list[tuple], ai_hints: dict[int, dict], signal_type: str
) -> str:
    """未核验候选的 AI 预判概览（如"2 条自购开箱候选（依据…）"）。"""
    counts: dict[str, int] = {}
    reason = ""
    for signal, evidence in candidates:
        if signal.signal_type != signal_type:
            continue
        hint = ai_hints.get(evidence.raw_content_id)
        if not hint or hint.get("kind") in (None, "none"):
            continue
        kind = hint["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        if not reason and hint.get("reason"):
            reason = hint["reason"]
    if not counts:
        return ""
    parts = [
        f"{count} 条{KIND_PREVIEW_LABELS.get(kind, kind)}"
        for kind, count in sorted(counts.items(), key=lambda item: -item[1])
    ]
    text = "、".join(parts) + "候选"
    return f"{text}（{reason}）" if reason else text


def _axis(
    key: str,
    label: str,
    state: str,
    state_label: str,
    summary: str,
    verified_ids: set[int],
    candidate_ids: set[int],
    updated_at,
    ai_preview: str = "",
) -> dict:
    return {
        "ai_preview": ai_preview,
        "key": key,
        "label": label,
        "state": state,
        "state_label": state_label,
        "summary": summary,
        "verified_count": len(verified_ids),
        "candidate_count": len(candidate_ids),
        "updated_at": updated_at,
        "signal_ids": sorted(verified_ids),
    }


async def build_fulfillment_snapshot(
    session: AsyncSession, product_id: int, *, signal_data: tuple[list, dict] | None = None
) -> dict:
    rows, ai_hints = signal_data if signal_data is not None else await load_product_signal_data(session, product_id)
    verified = [(s, e) for s, e, _, _ in rows if s.review_status == "verified"]
    candidates = [(s, e) for s, e, _, _ in rows if s.review_status == "candidate"]

    official = [(s, e) for s, e in verified if s.actor_type == "official" and s.signal_type in OFFICIAL_TYPES]
    official_candidates = {s.id for s, _ in candidates if s.signal_type in OFFICIAL_TYPES}
    if official:
        latest_official, _ = max(official, key=lambda item: item[0].occurred_at or item[0].updated_at)
        official_state = latest_official.signal_type
        official_label = SIGNAL_LABELS[latest_official.signal_type]
        official_summary = latest_official.summary
        official_updated = latest_official.occurred_at or latest_official.updated_at
    else:
        latest_official = None
        official_state = "no_verified_signal"
        official_label = "暂无已核验官方履约"
        official_summary = "未发现已核验的官方履约信息"
        official_updated = None

    receipt_by_source = {}
    for signal, evidence in verified:
        if (
            signal.signal_type == "consumer_received"
            and evidence.acquisition_type == "self_purchase"
        ):
            receipt_by_source.setdefault(evidence.independent_source_key, signal)
    receipt_sources = set(receipt_by_source)
    receipt_ids = {signal.id for signal in receipt_by_source.values()}
    receipt_candidates = {s.id for s, _ in candidates if s.signal_type == "consumer_received"}
    if len(receipt_sources) >= 3:
        consumer_state = "multiple_receipts"
        consumer_label = "多名独立用户到货"
        consumer_summary = f"已核验 {len(receipt_sources)} 个独立自购账号的到货证据"
    elif receipt_sources:
        consumer_state = "first_receipt" if len(receipt_sources) == 1 else "growing_receipts"
        consumer_label = "首位普通用户到货" if len(receipt_sources) == 1 else "已有消费者到货"
        consumer_summary = f"已核验 {len(receipt_sources)} 个独立自购账号的到货证据"
    else:
        consumer_state = "no_verified_receipt"
        consumer_label = "暂无已核验消费者到货"
        consumer_summary = "候选内容需确认是否为自购订单及真实到货"
    receipt_times = [
        s.occurred_at or s.updated_at
        for s, e in verified
        if s.id in receipt_ids
    ]

    sample_sources = {
        evidence.independent_source_key
        for signal, evidence in verified
        if signal.signal_type == "consumer_received" and evidence.acquisition_type == "seeded"
    }

    quality_by_source = {}
    for signal, evidence in verified:
        if signal.signal_type == "quality_issue":
            quality_by_source.setdefault(evidence.independent_source_key, signal)
    quality_sources = set(quality_by_source)
    quality_ids = {signal.id for signal in quality_by_source.values()}
    quality_candidates = {s.id for s, _ in candidates if s.signal_type == "quality_issue"}
    if len(quality_sources) >= 2:
        quality_state = "repeated_issue"
        quality_label = "发现重复质量问题"
        quality_summary = f"已核验 {len(quality_sources)} 个独立来源的质量反馈"
    elif quality_sources:
        quality_state = "isolated_issue"
        quality_label = "发现单例质量反馈"
        quality_summary = "当前只有一个已核验来源，暂不能推断为普遍问题"
    else:
        quality_candidate_sources = {
            evidence.independent_source_key
            for signal, evidence in candidates
            if signal.signal_type == "quality_issue"
        }
        if len(quality_candidate_sources) >= 2:
            quality_state = "candidate_attention"
            quality_label = "多账号反馈质量问题"
            quality_summary = (
                f"已有 {len(quality_candidate_sources)} 个独立来源的质量候选，尚未核验"
            )
        else:
            quality_state = "no_verified_issue"
            quality_label = "暂无已核验质量风险"
            quality_summary = "候选质量反馈尚未形成可公开结论"
    quality_times = [
        s.occurred_at or s.updated_at for s, _ in verified if s.id in quality_ids
    ]

    verified_times = [s.updated_at for s, _ in verified]
    product = await session.get(Product, product_id)
    return {
        "official": _axis(
            "official",
            "官方履约",
            official_state,
            official_label,
            official_summary,
            {s.id for s, _ in official},
            official_candidates,
            official_updated,
            _ai_preview(candidates, ai_hints, "presale_open")
            + _ai_preview(candidates, ai_hints, "shipping_started")
            + _ai_preview(candidates, ai_hints, "balance_open"),
        ),
        "consumer": _axis(
            "consumer",
            "消费者样本",
            consumer_state,
            consumer_label,
            consumer_summary,
            receipt_ids,
            receipt_candidates,
            max(receipt_times, default=None),
            _ai_preview(candidates, ai_hints, "consumer_received"),
        ),
        "quality": _axis(
            "quality",
            "质量风险",
            quality_state,
            quality_label,
            quality_summary,
            quality_ids,
            quality_candidates,
            max(quality_times, default=None),
            _ai_preview(candidates, ai_hints, "quality_issue"),
        ),
        "sample_count": len(sample_sources),
        "candidate_total": len({s.id for s, _ in candidates}),
        "last_verified_at": max(verified_times, default=None),
        "updated_at": (product.fulfillment_updated_at if product else None) or max((s.updated_at for s, _, _, _ in rows), default=None),
    }


async def list_product_signals(
    session: AsyncSession, product_id: int, *, include_rejected: bool = False,
    signal_data: tuple[list, dict] | None = None, include_notes: bool = True,
) -> list[dict]:
    rows, ai_hints = signal_data if signal_data is not None else await load_product_signal_data(session, product_id)
    last_notes: dict[int, str] = {}
    signal_ids = [signal.id for signal, _, _, _ in rows]
    if signal_ids and include_notes:
        revisions = await session.scalars(
            select(ProductSignalRevision)
            .where(ProductSignalRevision.signal_id.in_(signal_ids))
            .order_by(ProductSignalRevision.signal_id, ProductSignalRevision.id.desc())
        )
        for revision in revisions:
            last_notes.setdefault(revision.signal_id, revision.note)

    result = []
    seen: set[int] = set()
    for signal, evidence, raw, source in rows:
        if signal.id in seen or (signal.review_status == "rejected" and not include_rejected):
            continue
        seen.add(signal.id)
        result.append(
            {
                "id": signal.id,
                "product_id": signal.product_id,
                "community": signal.community,
                "signal_type": signal.signal_type,
                "signal_label": SIGNAL_LABELS.get(signal.signal_type, signal.signal_type),
                "actor_type": signal.actor_type,
                "review_status": signal.review_status,
                "occurred_at": signal.occurred_at,
                "summary": signal.summary,
                "confidence": signal.confidence,
                "source_method": signal.source_method,
                "acquisition_type": evidence.acquisition_type,
                "claim_quote": evidence.claim_quote,
                "content_id": raw.id,
                "content_title": signal.summary,
                "platform": raw.platform,
                "account": source.name if source else None,
                "url": raw.url,
                "review_note": last_notes.get(signal.id),
                "ai_hint": ai_hints.get(raw.id),
                "updated_at": signal.updated_at,
            }
        )
    return result


async def review_product_signal(
    session: AsyncSession,
    product_id: int,
    signal_id: int,
    *,
    review_status: str,
    actor_type: str | None,
    acquisition_type: str | None,
    summary: str | None,
    claim_quote: str | None,
    note: str,
) -> None:
    if review_status not in REVIEW_STATUSES:
        raise ValueError("invalid_review_status")
    product = await session.scalar(select(Product).where(Product.id == product_id).with_for_update())
    before = await build_fulfillment_snapshot(session, product_id)
    signal = await session.scalar(
        select(ProductSignal).where(
            ProductSignal.id == signal_id,
            ProductSignal.product_id == product_id,
            ProductSignal.active.is_(True),
        )
    )
    if signal is None:
        raise ValueError("signal_not_found")
    evidence = await session.scalar(
        select(ProductSignalEvidence).where(ProductSignalEvidence.signal_id == signal.id)
    )
    if evidence is None:
        raise ValueError("signal_evidence_not_found")
    raw = await session.get(RawContent, evidence.raw_content_id)
    if product is None or raw is None or raw.hidden or raw.community != product.community or signal.community != product.community:
        raise ValueError("signal_not_found")
    if acquisition_type is not None and acquisition_type not in ACQUISITION_TYPES:
        raise ValueError("invalid_acquisition_type")
    if actor_type is not None and actor_type not in ACTOR_TYPES:
        raise ValueError("invalid_actor_type")

    next_actor = actor_type or signal.actor_type
    next_acquisition = acquisition_type or evidence.acquisition_type
    next_summary = (summary or signal.summary).strip()
    next_quote = (claim_quote or evidence.claim_quote or "").strip()
    if review_status == "verified":
        if not next_summary or not next_quote:
            raise ValueError("verified_signal_requires_summary_and_quote")
        if signal.signal_type == "consumer_received" and next_acquisition == "unknown":
            raise ValueError("receipt_requires_acquisition_type")
        if (
            signal.signal_type == "consumer_received"
            and next_acquisition == "self_purchase"
            and next_actor != "user"
        ):
            raise ValueError("self_purchase_receipt_requires_user_actor")
        if signal.signal_type in OFFICIAL_TYPES and next_actor != "official":
            raise ValueError("official_signal_requires_official_actor")

    previous = signal.review_status
    signal.review_status = review_status
    signal.actor_type = next_actor
    signal.summary = next_summary
    signal.locked = review_status != "candidate"
    signal.updated_at = utcnow()
    product.fulfillment_updated_at = signal.updated_at
    evidence.acquisition_type = next_acquisition
    evidence.claim_quote = next_quote or None
    session.add(
        ProductSignalRevision(
            signal_id=signal.id,
            actor="admin",
            previous_status=previous,
            next_status=review_status,
            note=note.strip() or f"履约信号审核：{previous} → {review_status}",
        )
    )
    await session.flush()
    from app.services.fulfillment_revisions import record_product_fulfillment_change

    await record_product_fulfillment_change(session, product_id, before, await build_fulfillment_snapshot(session, product_id), "履约信号核验")


async def capture_content_fulfillment(session: AsyncSession, content_id: int) -> dict[int, dict]:
    """在原文隐藏、删除或修订前，保存受影响商品的公开状态。"""
    product_ids = set(await session.scalars(select(ProductSignal.product_id).join(
        ProductSignalEvidence, ProductSignalEvidence.signal_id == ProductSignal.id,
    ).where(ProductSignalEvidence.raw_content_id == content_id)))
    product_ids.update(await session.scalars(select(Event.product_id).join(Evidence).where(
        Evidence.raw_content_id == content_id, Event.product_id.is_not(None),
    )))
    raw = await session.get(RawContent, content_id)
    version = await session.scalar(select(ContentVersion).where(
        ContentVersion.raw_content_id == content_id,
    ).order_by(ContentVersion.version.desc()).limit(1))
    if raw and version:
        for product in await session.scalars(select(Product).where(Product.community == raw.community)):
            if matches_product_text(version.title, version.text, product.watch_terms or []):
                product_ids.add(product.id)
    result = {}
    for product_id in sorted(product_ids):
        await session.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
        result[product_id] = await build_fulfillment_snapshot(session, product_id)
    return result


async def refresh_content_fulfillment(
    session: AsyncSession, snapshots: dict[int, dict], *, reaggregate: bool = False,
) -> None:
    if reaggregate and snapshots:
        from app.services.events import AggStats, _aggregate_product, load_classified_contents

        contents = await load_classified_contents(session)
        for product_id, before in snapshots.items():
            product = await session.get(Product, product_id)
            if product:
                await _aggregate_product(session, product, contents, AggStats(), before=before)
        return
    for product_id, before in snapshots.items():
        await sync_product_signals(session, product_id, before=before)
