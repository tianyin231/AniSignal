"""事件聚合引擎 v1（规则版，不依赖 AI）。

两条聚合线：
1. 官方公告合并 —— 同类目、发布时间 72h 内、标题共享片段的官方内容并为一个事件；
   跨语言公告（中/英标题无共享词）v1 不合并，留给 AI 阶段。
2. 商品履约 —— 命中商品 watch_terms 且类目属履约类的内容挂到对应商品事件；
   开箱证据按送测/普通用户身份区分，独立账号数推进履约阶梯（§9.2）。

ensure 语义：事件只在创建时由引擎写标题/结论等字段，之后仅追加证据、
更新观测字段；manual_locked 的事件引擎完全跳过（§12 人工修正优先）。
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.models import (
    ANNOUNCE_CATEGORIES,
    Category,
    ContentVersion,
    Event,
    EventRevision,
    Evidence,
    FULFILLMENT_CATEGORIES,
    FULFILLMENT_STAGES,
    Product,
    RawContent,
    Source,
)

logger = logging.getLogger(__name__)

_MERGE_WINDOW = timedelta(hours=72)
_MIN_SHARED_SUBSTR = 4  # 标题最长公共子串达到该长度视为同主题

_REGION_OF_PLATFORM = {
    "bilibili": "cn",
    "miyoushe": "cn",
    "kuro_cn": "cn",
    "kuro_global": "global",
    "youtube": "global",
}


@dataclass
class ContentRow:
    """聚合引擎视角的一条内容（最新版本 + 身份）。"""

    raw_id: int
    platform: str
    external_id: str
    url: str | None
    published_at: datetime | None
    collected_at: datetime
    title: str
    category: str | None
    identity: str
    source_name: str | None
    source_external_id: str | None


@dataclass
class AggStats:
    events_created: int = 0
    evidences_added: int = 0
    evidences_removed: int = 0
    events_deactivated: int = 0
    revisions_written: int = 0
    stage_advances: int = 0
    skipped_locked: int = 0
    touched_event_ids: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.touched_event_ids is None:
            self.touched_event_ids = []


CATEGORY_LABELS = {
    "official_announce": "官方公告",
    "version_update": "版本/活动",
    "presale": "预售",
    "balance": "尾款",
    "delay": "延期",
    "shipping": "发货",
    "arrived": "到货",
    "unboxing": "开箱",
    "review": "评测",
    "quality": "质量/售后",
}


def _build_facts(category: str, cluster: list, product=None) -> list:
    """事件关键事实（结构化模板；AI 摘要可覆盖）。"""
    first = sorted(cluster, key=_sort_key)[0]
    facts = []
    if product is not None:
        facts.append({"label": "商品", "value": product.name})
    facts.append({"label": "类目", "value": CATEGORY_LABELS.get(category, category)})
    facts.append({"label": "首见时间", "value": (first.published_at or first.collected_at).strftime("%Y-%m-%d %H:%M")})
    facts.append({"label": "证据数", "value": f"{len(cluster)} 条"})
    facts.append({"label": "来源平台", "value": "、".join(sorted({r.platform for r in cluster}))})
    return facts


async def load_classified_contents(session: AsyncSession) -> list[ContentRow]:
    """全部内容 + 最新版本（含分类）+ 来源身份。"""
    excluded_ids = set(
        await session.scalars(
            select(Evidence.raw_content_id).where(Evidence.role == "excluded")
        )
    )
    rows = await session.execute(
        select(RawContent, ContentVersion, Source)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id, isouter=True)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .order_by(RawContent.id, ContentVersion.version.desc())
    )
    seen: set[int] = set()
    result: list[ContentRow] = []
    for raw, version, source in rows:
        if raw.id in seen or raw.id in excluded_ids or version is None:
            continue
        seen.add(raw.id)
        published = raw.published_at
        if published is not None and published.tzinfo is not None:
            published = to_naive_utc(published)
        result.append(
            ContentRow(
                raw_id=raw.id,
                platform=raw.platform,
                external_id=raw.external_id,
                url=raw.url,
                published_at=published,
                collected_at=raw.collected_at,
                title=(version.title or raw.external_id),
                category=version.category,
                identity=(source.identity if source else "unknown"),
                source_name=(source.name if source else None),
                source_external_id=(source.external_id if source else None),
            )
        )
    return result


_TITLE_BOILERPLATE = (
    "《鸣潮》",
    "鸣潮",
    "先约电台",
    "官方",
    "公告",
    "说明",
    "声明",
    "活动",
)


def _normalize_title(title: str) -> str:
    """移除游戏名、版本号和公告套话，只保留可区分主题的字符。"""
    value = unicodedata.normalize("NFKC", title).lower()
    value = re.sub(r"#[^#]+#", "", value)
    value = re.sub(r"\d+(?:\.\d+)?\s*版本", "", value)
    for phrase in _TITLE_BOILERPLATE:
        value = value.replace(phrase.lower(), "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _shared_title(a: str, b: str) -> bool:
    """去除公共套话后，最长公共子串达到阈值才视为同主题。"""
    a = _normalize_title(a)
    b = _normalize_title(b)
    if len(a) < _MIN_SHARED_SUBSTR or len(b) < _MIN_SHARED_SUBSTR:
        return False
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
        if best >= 12:
            break
    return best >= _MIN_SHARED_SUBSTR


def _sort_key(row: ContentRow) -> datetime:
    return row.published_at or row.collected_at


def cluster_announcements(items: list[ContentRow]) -> list[list[ContentRow]]:
    """同类目内聚类：时间相近，且与组内每条内容都共享有效主题。"""
    clusters: list[list[ContentRow]] = []
    for row in sorted(items, key=_sort_key):
        for cluster in clusters:
            last = cluster[-1]
            if (
                _sort_key(row) - _sort_key(last) <= _MERGE_WINDOW
                and all(_shared_title(row.title, item.title) for item in cluster)
            ):
                cluster.append(row)
                break
        else:
            clusters.append([row])
    return clusters


_CATEGORY_CONCLUSION = {
    Category.DELAY: "出现发货时间推迟的相关信息",
    Category.PRESALE: "预售相关动态",
    Category.BALANCE: "尾款支付相关动态",
    Category.SHIPPING: "发货流程相关信息",
    Category.ARRIVED: "用户到货反馈",
    Category.UNBOXING: "开箱内容出现",
    Category.REVIEW: "评测内容出现",
    Category.QUALITY: "质量或售后反馈出现",
    Category.OFFICIAL_ANNOUNCE: "官方发布新公告",
    Category.VERSION_UPDATE: "官方版本/活动信息",
}


async def run_aggregation(session: AsyncSession) -> AggStats:
    """全量扫描并维护事件（ensure 语义），调用方负责 commit。"""
    from app.services.classify import seed_defaults

    stats = AggStats()
    await seed_defaults(session)
    contents = await load_classified_contents(session)

    await _aggregate_official(session, contents, stats)

    products = list((await session.execute(select(Product))).scalars())
    for product in products:
        if product.watch_terms:
            await _aggregate_product(session, product, contents, stats)
    await _backfill_facts(session, stats)
    return stats


async def _backfill_facts(session: AsyncSession, stats: AggStats) -> None:
    """为缺少 facts 的存量事件补模板关键事实（不覆盖已有的/AI 生成的）。"""
    events = list(
        (
            await session.execute(
                select(Event).where(Event.is_active.is_(True), Event.manual_locked.is_(False))
            )
        ).scalars()
    )
    missing = [e for e in events if not e.facts]
    if not missing:
        return
    products = {p.id: p for p in (await session.execute(select(Product))).scalars()}

    rows = await session.execute(
        select(RawContent, Evidence)
        .join(Evidence, Evidence.raw_content_id == RawContent.id)
        .where(Evidence.event_id.in_([e.id for e in missing]))
        .order_by(RawContent.published_at.asc())
    )
    evidences_by_event: dict[int, list[RawContent]] = {}
    for raw, evidence in rows:
        evidences_by_event.setdefault(evidence.event_id, []).append(raw)

    for event in missing:
        raws = evidences_by_event.get(event.id) or []
        if not raws:
            continue
        facts = []
        product = products.get(event.product_id) if event.product_id else None
        if product is not None:
            facts.append({"label": "商品", "value": product.name})
        facts.append({"label": "类目", "value": CATEGORY_LABELS.get(event.category, event.category)})
        first_time = min((r.published_at or r.collected_at for r in raws), default=None)
        if first_time:
            facts.append({"label": "首见时间", "value": first_time.strftime("%Y-%m-%d %H:%M")})
        facts.append({"label": "证据数", "value": f"{len(raws)} 条"})
        facts.append({"label": "来源平台", "value": "、".join(sorted({r.platform for r in raws}))})
        event.facts = facts
    stats.revisions_written += 0  # 模板回填不写修订（非结论变化）


async def _aggregate_official(
    session: AsyncSession, contents: list[ContentRow], stats: AggStats
) -> None:
    """按当前聚类结果精确对账官方事件，允许移除历史误挂证据。"""
    official = [
        row
        for row in contents
        if row.identity == "official" and row.category in ANNOUNCE_CATEGORIES
    ]

    existing = list(
        await session.scalars(
            select(Event).where(
                Event.is_active.is_(True),
                Event.product_id.is_(None),
                Event.category.in_(ANNOUNCE_CATEGORIES),
            )
        )
    )
    evidence_by_event: dict[int, set[int]] = {event.id: set() for event in existing}
    if existing:
        evidence_rows = await session.execute(
            select(Evidence.event_id, Evidence.raw_content_id).where(
                Evidence.event_id.in_([event.id for event in existing])
            )
        )
        for event_id, raw_id in evidence_rows:
            evidence_by_event[event_id].add(raw_id)

    locked_raw_ids = set().union(
        *(evidence_by_event[event.id] for event in existing if event.manual_locked)
    ) if any(event.manual_locked for event in existing) else set()
    if locked_raw_ids:
        official = [row for row in official if row.raw_id not in locked_raw_ids]

    by_category: dict[str, list[ContentRow]] = {}
    for row in official:
        by_category.setdefault(row.category, []).append(row)

    unlocked = [event for event in existing if not event.manual_locked]
    used_event_ids: set[int] = set()
    for category, items in by_category.items():
        for cluster in cluster_announcements(items):
            raw_ids = {row.raw_id for row in cluster}
            candidates = [
                event
                for event in unlocked
                if event.id not in used_event_ids and event.category == category
            ]
            event = max(
                candidates,
                key=lambda item: len(evidence_by_event[item.id] & raw_ids),
                default=None,
            )
            if event is not None and not (evidence_by_event[event.id] & raw_ids):
                event = None
            if event is None:
                first = sorted(cluster, key=_sort_key)[0]
                event = Event(
                    title=first.title[:255],
                    category=category,
                    product_id=None,
                    identity="official",
                    confidence="high",
                    confidence_reason="官方来源公告",
                    conclusion=_CATEGORY_CONCLUSION.get(category, "官方公告"),
                    regions=sorted(_regions_of(cluster)),
                    facts=_build_facts(category, cluster),
                    first_seen_at=first.published_at or first.collected_at,
                    last_updated_at=utcnow(),
                )
                session.add(event)
                await session.flush()
                evidence_by_event[event.id] = set()
                stats.events_created += 1
                _add_revision(session, event, stats, "事件由聚合引擎创建")

            used_event_ids.add(event.id)
            await _reconcile_official_event(
                session, event, cluster, evidence_by_event[event.id], stats
            )

    for event in unlocked:
        if event.id in used_event_ids:
            continue
        event.is_active = False
        event.last_updated_at = utcnow()
        stats.events_deactivated += 1
        _add_revision(session, event, stats, "当前规则下已无匹配证据，事件停用")


async def _reconcile_official_event(
    session: AsyncSession,
    event: Event,
    cluster: list[ContentRow],
    existing_ids: set[int],
    stats: AggStats,
) -> None:
    expected_ids = {row.raw_id for row in cluster}
    removed_ids = existing_ids - expected_ids
    added_rows = [row for row in cluster if row.raw_id not in existing_ids]
    first = sorted(cluster, key=_sort_key)[0]
    expected_title = first.title[:255]
    expected_facts = _build_facts(event.category, cluster)
    expected_regions = sorted(_regions_of(cluster))
    metadata_changed = any(
        (
            event.title != expected_title,
            event.identity != "official",
            event.confidence != "high",
            event.confidence_reason != "官方来源公告",
            event.conclusion != _CATEGORY_CONCLUSION.get(event.category, "官方公告"),
            (event.regions or []) != expected_regions,
            (event.facts or []) != expected_facts,
            event.first_seen_at != (first.published_at or first.collected_at),
        )
    )

    if removed_ids:
        await session.execute(
            delete(Evidence).where(
                Evidence.event_id == event.id,
                Evidence.raw_content_id.in_(removed_ids),
            )
        )
        stats.evidences_removed += len(removed_ids)
    for row in added_rows:
        session.add(Evidence(event_id=event.id, raw_content_id=row.raw_id))
    stats.evidences_added += len(added_rows)

    if not removed_ids and not added_rows and not metadata_changed:
        return

    event.title = expected_title
    event.identity = "official"
    event.confidence = "high"
    event.confidence_reason = "官方来源公告"
    event.conclusion = _CATEGORY_CONCLUSION.get(event.category, "官方公告")
    event.regions = expected_regions
    event.facts = expected_facts
    event.first_seen_at = first.published_at or first.collected_at
    event.last_updated_at = utcnow()
    event.summary = None
    event.summary_status = None
    event.summary_updated_at = None
    if event.id not in stats.touched_event_ids:
        stats.touched_event_ids.append(event.id)
    changes = []
    if added_rows:
        changes.append(f"新增 {len(added_rows)} 条")
    if removed_ids:
        changes.append(f"移除 {len(removed_ids)} 条")
    if metadata_changed and not changes:
        changes.append("同步标题与事实")
    _add_revision(session, event, stats, "证据对账：" + "，".join(changes))


async def _aggregate_product(
    session: AsyncSession, product: Product, contents: list[ContentRow], stats: AggStats
) -> None:
    matched = [
        row
        for row in contents
        if row.category in FULFILLMENT_CATEGORIES and _matches_product(row, product)
    ]
    by_category: dict[str, list[ContentRow]] = {}
    for row in matched:
        by_category.setdefault(row.category, []).append(row)

    for category, items in by_category.items():
        identity = "official" if all(r.identity == "official" for r in items) else "user"
        confidence, reason = _product_confidence(category, items)
        await _ensure_event(
            session,
            category=category,
            product_id=product.id,
            cluster=items,
            stats=stats,
            product=product,
            title=f"{product.name} · {_CATEGORY_CONCLUSION.get(category, category)}",
            identity=identity,
            confidence=confidence,
            confidence_reason=reason,
            conclusion=_product_conclusion(category, items, product),
        )

    await _advance_stage(session, product, contents, stats)


def _matches_product(row: ContentRow, product: Product) -> bool:
    return any(term and term in row.title for term in (product.watch_terms or []))


def _distinct_user_sources(items: list[ContentRow]) -> list[str]:
    return sorted(
        {
            row.source_external_id or f"{row.platform}:{row.external_id}"
            for row in items
            if row.identity not in ("official", "seeded")
        }
    )


def _product_confidence(category: str, items: list[ContentRow]) -> tuple[str, str]:
    if category in ("unboxing", "arrived"):
        users = _distinct_user_sources(items)
        if len(users) >= 5:
            return "high", f"{len(users)} 个独立账号的证据"
        if len(users) >= 3:
            return "medium", f"{len(users)} 个独立账号的证据"
        if len(users) >= 1:
            return "low", "单一账号证据，需人工复核账号身份与商品对应关系（§9.2）"
        return "low", "仅有送测样品证据，不代表消费者订单履约"
    if len(items) >= 2:
        return "medium", f"{len(items)} 条相关内容"
    return "low", "单一来源，待更多证据"


def _product_conclusion(category: str, items: list[ContentRow], product: Product) -> str:
    if category in ("unboxing", "arrived"):
        users = _distinct_user_sources(items)
        seeded = any(row.identity == "seeded" for row in items)
        parts = []
        if seeded:
            parts.append("出现送测样品开箱（仅证明实物样品存在）")
        if users:
            parts.append(f"{len(users)} 个独立账号发布到货/开箱内容")
        if parts:
            return f"{product.name}：" + "；".join(parts)
    return _CATEGORY_CONCLUSION.get(category, "")


async def _advance_stage(
    session: AsyncSession, product: Product, contents: list[ContentRow], stats: AggStats
) -> None:
    """按开箱/到货证据推进履约阶梯；stage_hint ≥ 0 时以管理端指定为准。"""
    unboxing = [
        row
        for row in contents
        if row.category in ("unboxing", "arrived") and _matches_product(row, product)
    ]
    seeded = any(row.identity == "seeded" for row in unboxing)
    users = _distinct_user_sources(unboxing)
    stage = 0
    if seeded:
        stage = max(stage, 1)
    if len(users) >= 1:
        stage = max(stage, 2)
    if len(users) >= 3:
        stage = max(stage, 3)

    effective = stage if product.stage_hint < 0 else product.stage_hint
    if effective <= product.computed_stage:
        return

    previous_stage = min(product.computed_stage, len(FULFILLMENT_STAGES) - 1)
    product.computed_stage = effective
    stats.stage_advances += 1

    events = list(
        (
            await session.execute(
                select(Event).where(
                    Event.category.in_(["unboxing", "arrived"]),
                    Event.is_active.is_(True),
                    Event.product_id == product.id,
                )
            )
        ).scalars()
    )
    label = FULFILLMENT_STAGES[min(effective, len(FULFILLMENT_STAGES) - 1)]
    for event in events:
        event.last_updated_at = utcnow()
        if event.manual_locked:
            continue
        event.status_label = label
        if effective >= 2:
            event.importance = True
        note = (
            f"履约状态推进：{FULFILLMENT_STAGES[previous_stage]} → {label}"
            if previous_stage > 0 or product.computed_stage != effective
            else f"履约状态初始化：{label}"
        )
        _add_revision(session, event, stats, note)


async def _ensure_event(
    session: AsyncSession,
    *,
    category: str,
    product_id: int | None,
    cluster: list[ContentRow],
    stats: AggStats,
    title: str,
    product=None,
    identity: str,
    confidence: str,
    confidence_reason: str,
    conclusion: str,
) -> None:
    raw_ids = {row.raw_id for row in cluster}
    event = await _find_active_event(session, category, product_id, raw_ids)

    created = False
    if event is None:
        first = sorted(cluster, key=_sort_key)[0]
        event = Event(
            title=title[:255],
            category=category,
            product_id=product_id,
            identity=identity,
            confidence=confidence,
            confidence_reason=confidence_reason,
            conclusion=conclusion,
            regions=sorted(_regions_of(cluster)),
            facts=_build_facts(category, cluster, product),
            first_seen_at=first.published_at or first.collected_at,
            last_updated_at=utcnow(),
        )
        session.add(event)
        await session.flush()  # 拿 id
        _add_revision(session, event, stats, "事件由聚合引擎创建")
        stats.events_created += 1
        created = True
        if event.id not in stats.touched_event_ids:
            stats.touched_event_ids.append(event.id)
    elif event.manual_locked:
        stats.skipped_locked += 1
        return

    existing_ids: set[int] = set()
    if not created:
        existing_ids = set(
            await session.scalars(
                select(Evidence.raw_content_id).where(Evidence.event_id == event.id)
            )
        )
    new_rows = [row for row in cluster if row.raw_id not in existing_ids]
    for row in new_rows:
        session.add(Evidence(event_id=event.id, raw_content_id=row.raw_id))
    if new_rows:
        stats.evidences_added += len(new_rows)
        if event.id not in stats.touched_event_ids:
            stats.touched_event_ids.append(event.id)
        event.last_updated_at = utcnow()
        event.regions = sorted(set(event.regions or []) | _regions_of(new_rows))
        _add_revision(
            session,
            event,
            stats,
            f"新增证据 {len(new_rows)} 条（{', '.join(sorted({r.platform for r in new_rows}))}）",
        )


async def _find_active_event(
    session: AsyncSession, category: str, product_id: int | None, raw_ids: set[int]
) -> Event | None:
    """同类目同范围的活跃事件中，找证据有交集的那个。

    只按证据重叠合并：不同主题的公告即使同类目也各自成事件（宁缺勿滥）；
    商品事件同类目本就单一，靠重叠即可延续。
    """
    product_filter = (
        Event.product_id.is_(None) if product_id is None else Event.product_id == product_id
    )
    events = list(
        (
            await session.execute(
                select(Event).where(
                    Event.category == category,
                    Event.is_active.is_(True),
                    product_filter,
                )
            )
        ).scalars()
    )
    for event in events:
        overlap = await session.scalar(
            select(func.count(Evidence.id)).where(
                Evidence.event_id == event.id, Evidence.raw_content_id.in_(raw_ids)
            )
        )
        if overlap:
            return event
    return None


def _add_revision(session: AsyncSession, event: Event, stats: AggStats, note: str) -> None:
    session.add(EventRevision(event_id=event.id, actor="engine", note=note))
    stats.revisions_written += 1


def _regions_of(rows: list[ContentRow]) -> set[str]:
    return {_REGION_OF_PLATFORM.get(row.platform, "cn") for row in rows}


def _refresh_manual_event(event: Event, rows: list[ContentRow], product=None) -> None:
    """证据人工变化后，用剩余证据重建可确定的事件字段。"""
    first = sorted(rows, key=_sort_key)[0]
    event.identity = "official" if all(row.identity == "official" for row in rows) else "user"
    if product is not None:
        event.confidence, event.confidence_reason = _product_confidence(event.category, rows)
        event.conclusion = _product_conclusion(event.category, rows, product)
    elif event.identity == "official":
        event.confidence = "high"
        event.confidence_reason = "官方来源公告"
        event.conclusion = _CATEGORY_CONCLUSION.get(event.category, "官方公告")
    event.title = first.title[:255]
    event.regions = sorted(_regions_of(rows))
    event.facts = _build_facts(event.category, rows, product)
    event.first_seen_at = first.published_at or first.collected_at
    event.last_updated_at = utcnow()
    event.summary = None
    event.summary_status = None
    event.summary_updated_at = None
    event.manual_locked = True


async def split_event(
    session: AsyncSession, event_id: int, content_ids: list[int]
) -> Event:
    """把所选证据移动到新的人工锁定事件。"""
    event = await session.get(Event, event_id)
    if event is None:
        raise ValueError("event_not_found")

    requested_ids = set(content_ids)
    evidences = list(
        await session.scalars(
            select(Evidence).where(
                Evidence.event_id == event_id,
                Evidence.role != "excluded",
            )
        )
    )
    selected = [evidence for evidence in evidences if evidence.raw_content_id in requested_ids]
    if len(selected) != len(requested_ids):
        raise ValueError("evidence_not_found")
    if len(selected) == len(evidences):
        raise ValueError("cannot_split_all_evidences")

    content_by_id = {
        row.raw_id: row for row in await load_classified_contents(session)
    }
    selected_rows = [content_by_id[evidence.raw_content_id] for evidence in selected]
    remaining_rows = [
        content_by_id[evidence.raw_content_id]
        for evidence in evidences
        if evidence.raw_content_id not in requested_ids
    ]
    product = await session.get(Product, event.product_id) if event.product_id else None
    first = sorted(selected_rows, key=_sort_key)[0]
    split = Event(
        title=first.title[:255],
        category=event.category,
        product_id=event.product_id,
        identity=event.identity,
        importance=event.importance,
        confidence=event.confidence,
        confidence_reason=event.confidence_reason,
        conclusion=event.conclusion,
        status_label=event.status_label,
        facts=_build_facts(event.category, selected_rows, product),
        regions=sorted(_regions_of(selected_rows)),
        manual_locked=True,
        first_seen_at=first.published_at or first.collected_at,
        last_updated_at=utcnow(),
    )
    session.add(split)
    _refresh_manual_event(split, selected_rows, product)
    await session.flush()
    for evidence in selected:
        evidence.event_id = split.id
        evidence.role = "support"

    _refresh_manual_event(event, remaining_rows, product)
    session.add_all(
        [
            EventRevision(
                event_id=event.id,
                actor="admin",
                note=f"人工拆出 {len(selected)} 条证据至事件 #{split.id}",
            ),
            EventRevision(
                event_id=split.id,
                actor="admin",
                note=f"从事件 #{event.id} 人工拆分，迁入 {len(selected)} 条证据",
            ),
        ]
    )
    return split


async def exclude_evidence(
    session: AsyncSession, event_id: int, content_id: int
) -> tuple[int, bool]:
    """从事件移除证据，并持久排除其自动聚合。"""
    event = await session.get(Event, event_id)
    if event is None:
        raise ValueError("event_not_found")
    evidence = await session.scalar(
        select(Evidence).where(
            Evidence.event_id == event_id,
            Evidence.raw_content_id == content_id,
            Evidence.role != "excluded",
        )
    )
    if evidence is None:
        raise ValueError("evidence_not_found")

    other_evidences = list(
        await session.scalars(
            select(Evidence).where(
                Evidence.event_id == event_id,
                Evidence.raw_content_id != content_id,
                Evidence.role != "excluded",
            )
        )
    )
    evidence.role = "excluded"
    evidence.note = "管理员从事件中排除"
    event.manual_locked = True
    event.summary = None
    event.summary_status = None
    event.summary_updated_at = None
    event.last_updated_at = utcnow()

    if other_evidences:
        content_by_id = {
            row.raw_id: row for row in await load_classified_contents(session)
        }
        remaining_rows = [content_by_id[item.raw_content_id] for item in other_evidences]
        product = await session.get(Product, event.product_id) if event.product_id else None
        _refresh_manual_event(event, remaining_rows, product)
    else:
        event.is_active = False
        event.facts = []

    session.add(
        EventRevision(
            event_id=event.id,
            actor="admin",
            note=f"人工移除并排除证据内容 #{content_id}",
        )
    )
    return len(other_evidences), not other_evidences
