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
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
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
    revisions_written: int = 0
    stage_advances: int = 0
    skipped_locked: int = 0


async def load_classified_contents(session: AsyncSession) -> list[ContentRow]:
    """全部内容 + 最新版本（含分类）+ 来源身份。"""
    rows = await session.execute(
        select(RawContent, ContentVersion, Source)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id, isouter=True)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .order_by(RawContent.id, ContentVersion.version.desc())
    )
    seen: set[int] = set()
    result: list[ContentRow] = []
    for raw, version, source in rows:
        if raw.id in seen or version is None:
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


def _shared_title(a: str, b: str) -> bool:
    """最长公共子串 ≥ _MIN_SHARED_SUBSTR 视为同主题（中文标题友好）。"""
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
    """同类目内按时间链式聚类：相邻 ≤72h 且标题共享片段。"""
    clusters: list[list[ContentRow]] = []
    for row in sorted(items, key=_sort_key):
        for cluster in clusters:
            last = cluster[-1]
            if (
                _sort_key(row) - _sort_key(last) <= _MERGE_WINDOW
                and _shared_title(row.title, last.title)
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
    return stats


async def _aggregate_official(
    session: AsyncSession, contents: list[ContentRow], stats: AggStats
) -> None:
    official = [
        row
        for row in contents
        if row.identity == "official" and row.category in ANNOUNCE_CATEGORIES
    ]
    by_category: dict[str, list[ContentRow]] = {}
    for row in official:
        by_category.setdefault(row.category, []).append(row)

    for category, items in by_category.items():
        for cluster in cluster_announcements(items):
            await _ensure_event(
                session,
                category=category,
                product_id=None,
                cluster=cluster,
                stats=stats,
                title=sorted(cluster, key=_sort_key)[0].title,
                identity="official",
                confidence="high",
                confidence_reason="官方来源公告",
                conclusion=_CATEGORY_CONCLUSION.get(category, "官方公告"),
            )


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
            first_seen_at=first.published_at or first.collected_at,
            last_updated_at=utcnow(),
        )
        session.add(event)
        await session.flush()  # 拿 id
        _add_revision(session, event, stats, "事件由聚合引擎创建")
        stats.events_created += 1
        created = True
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
    """同类目同范围的活跃事件中，找证据有交集的那个；官方类唯一活跃事件可延续。"""
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
    if product_id is None and len(events) == 1:
        return events[0]
    return None


def _add_revision(session: AsyncSession, event: Event, stats: AggStats, note: str) -> None:
    session.add(EventRevision(event_id=event.id, actor="engine", note=note))
    stats.revisions_written += 1


def _regions_of(rows: list[ContentRow]) -> set[str]:
    return {_REGION_OF_PLATFORM.get(row.platform, "cn") for row in rows}
