"""公开 API：内容流、来源健康、统计。只暴露可公开的信息（无错误详情、无内部字段）。

公开页当前展示"原始内容流"；事件聚合视图属于事件引擎阶段（设计文档 §4）。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CollectTarget, ContentVersion, RawContent, Source
from app.schemas.api import (
    ContentDetailOut,
    EventListOut,
    EventOut,
    ProductListOut,
    ProductOut,
    PublicContentItem,
    PublicContentList,
    PublicSource,
    PublicSourceList,
    PublicStats,
)

router = APIRouter(prefix="/api")

_EXCERPT_LEN = 140


@router.get("/contents", response_model=PublicContentList)
async def list_contents(
    platform: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    identity: str | None = Query(default=None, description="official/user/... 来源身份过滤"),
    q: str | None = Query(default=None, max_length=100, description="标题关键词"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    conditions = []
    if platform:
        conditions.append(RawContent.platform == platform)
    if content_type:
        conditions.append(RawContent.content_type == content_type)
    if identity:
        conditions.append(Source.identity == identity)
    if q:
        conditions.append(ContentVersion.title.like(f"%{q}%"))

    rows = await session.execute(
        select(RawContent, Source.name, Source.identity, ContentVersion)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id, isouter=True)
        .where(*conditions)
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc(), ContentVersion.version.desc())
        .limit(limit * 3)  # 多取以容纳同一内容的多版本行，Python 侧去重截断
        .offset(offset)
    )
    items: list[PublicContentItem] = []
    seen: set[int] = set()
    for raw, source_name, source_identity, version in rows:
        if raw.id in seen or len(items) >= limit:
            continue
        seen.add(raw.id)
        normalized = version.normalized_json if version else None
        text = (version.text if version else None) or str((normalized or {}).get("text") or "")
        media = (normalized or {}).get("media") or []
        items.append(
            PublicContentItem(
                id=raw.id,
                platform=raw.platform,
                content_type=raw.content_type,
                external_id=raw.external_id,
                url=raw.url,
                title=(version.title if version else None) or raw.external_id,
                text_excerpt=_excerpt(text),
                published_at=raw.published_at,
                collected_at=raw.collected_at,
                source_name=source_name,
                identity=source_identity or "unknown",
                metrics=None,  # 指标单独批量查，见下
                cover=media[0] if isinstance(media, list) and media else None,
            )
        )

    metrics_map = await _latest_metrics(session, [i.id for i in items])
    for item in items:
        item.metrics = metrics_map.get(item.id)

    count_conditions = []
    if platform:
        count_conditions.append(RawContent.platform == platform)
    if content_type:
        count_conditions.append(RawContent.content_type == content_type)
    if identity:
        count_conditions.append(Source.identity == identity)
    if q:
        count_conditions.append(ContentVersion.title.like(f"%{q}%"))
    count_sq = (
        select(func.count(func.distinct(RawContent.id)))
        .join(Source, RawContent.source_id == Source.id, isouter=True)
    )
    if q:
        count_sq = count_sq.join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
    count_sq = count_sq.where(*count_conditions)
    total = await session.scalar(count_sq) or 0
    return PublicContentList(items=items, total=total)


async def _latest_metrics(session: AsyncSession, content_ids: list[int]) -> dict[int, dict]:
    from app.models import MetricSnapshot

    if not content_ids:
        return {}
    rows = await session.execute(
        select(MetricSnapshot)
        .where(MetricSnapshot.raw_content_id.in_(content_ids))
        .order_by(MetricSnapshot.raw_content_id, MetricSnapshot.id.desc())
    )
    result: dict[int, dict] = {}
    for snapshot in rows.scalars():
        result.setdefault(snapshot.raw_content_id, snapshot.metrics)
    return result


def _excerpt(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    return text[:_EXCERPT_LEN] + ("…" if len(text) > _EXCERPT_LEN else "")


@router.get("/sources", response_model=PublicSourceList)
async def list_sources(session: AsyncSession = Depends(get_session)):
    """公开的来源健康：已接入来源、最近成功采集时间与不可用状态（设计文档 §6/§16）。

    不暴露 last_error 等内部细节。
    """
    rows = await session.execute(
        select(CollectTarget).order_by(CollectTarget.platform, CollectTarget.id)
    )
    items = [
        PublicSource(
            name=target.display_name or target.target_value,
            platform=target.platform,
            target_type=target.target_type,
            enabled=target.enabled,
            last_success_at=target.last_success_at,
            consecutive_failures=target.consecutive_failures,
        )
        for target in rows.scalars()
    ]
    return PublicSourceList(items=items)


_ALLOWED_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "div", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "q",
    "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "th",
    "thead", "tr", "u", "ul",
]
_ALLOWED_ATTRS = {"a": ["href", "title"], "img": ["src", "alt"]}


def _sanitize_html(text: str) -> str | None:
    if "<" not in text or ">" not in text:
        return None
    import bleach

    return bleach.clean(text, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


@router.get("/contents/{content_id}", response_model=ContentDetailOut)
async def get_content_detail(content_id: int, session: AsyncSession = Depends(get_session)):
    """站内内容详情：解析后的正文/媒体/指标历史/关联事件；外链仅作参考出处。"""
    from fastapi import HTTPException

    from app.models import Event as EventModel
    from app.models import Evidence, MetricSnapshot
    from app.schemas.api import ContentDetailOut, ContentMetricPoint

    row = await session.execute(
        select(RawContent, ContentVersion, Source)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id, isouter=True)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(RawContent.id == content_id)
        .order_by(ContentVersion.version.desc())
        .limit(1)
    )
    result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail="content not found")
    raw, version, source = result

    normalized = (version.normalized_json if version else None) or {}
    text = (version.text if version else None) or str(normalized.get("text") or "")
    media = [m for m in (normalized.get("media") or []) if isinstance(m, str)]

    history_rows = await session.execute(
        select(MetricSnapshot)
        .where(MetricSnapshot.raw_content_id == content_id)
        .order_by(MetricSnapshot.id.desc())
        .limit(10)
    )
    history = [
        ContentMetricPoint(captured_at=snap.captured_at, metrics=snap.metrics)
        for snap in history_rows.scalars()
    ]
    latest_metrics = history[0].metrics if history else None

    event_row = await session.execute(
        select(EventModel, Evidence)
        .join(Evidence, Evidence.event_id == EventModel.id)
        .where(Evidence.raw_content_id == content_id, EventModel.is_active.is_(True))
        .limit(1)
    )
    event_pair = event_row.first()
    event = None
    if event_pair:
        event_model = event_pair[0]
        event = {
            "id": event_model.id,
            "title": event_model.title,
            "category": event_model.category,
            "confidence": event_model.confidence,
        }

    return ContentDetailOut(
        id=raw.id,
        platform=raw.platform,
        content_type=raw.content_type,
        external_id=raw.external_id,
        url=raw.url,
        title=(version.title if version else None) or raw.external_id,
        text=text or None,
        text_html=_sanitize_html(text) if text else None,
        published_at=raw.published_at,
        collected_at=raw.collected_at,
        last_seen_at=raw.last_seen_at,
        source_name=(source.name if source else None),
        source_identity=(source.identity if source else None),
        identity=(source.identity if source else "unknown"),
        media=media,
        metrics=latest_metrics,
        metrics_history=list(reversed(history)),
        event=event,
        version=version.version if version else None,
    )

@router.get("/events", response_model=EventListOut)
async def list_events(
    category: str | None = Query(default=None),
    product_slug: str | None = Query(default=None),
    importance: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    from app.models import Event as EventModel
    from app.models import Product as ProductModel
    from app.services.views import events_to_out

    conditions = [EventModel.is_active.is_(True)]
    if category:
        conditions.append(EventModel.category == category)
    if product_slug:
        product = await session.scalar(
            select(ProductModel).where(ProductModel.slug == product_slug)
        )
        conditions.append(EventModel.product_id == (product.id if product else -1))
    if importance is not None:
        conditions.append(EventModel.importance.is_(importance))
    if q:
        conditions.append(EventModel.title.like(f"%{q}%"))

    total = await session.scalar(
        select(func.count(EventModel.id)).where(*conditions)
    ) or 0
    events = list(
        (
            await session.execute(
                select(EventModel)
                .where(*conditions)
                .order_by(EventModel.last_updated_at.desc(), EventModel.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    items = await events_to_out(session, events)
    return EventListOut(items=items, total=total)


@router.get("/events/{event_id}", response_model=EventOut)
async def get_event(event_id: int, session: AsyncSession = Depends(get_session)):
    from app.models import Event as EventModel
    from app.services.views import events_to_out

    event = await session.get(EventModel, event_id)
    if event is None or not event.is_active:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="event not found")
    return (await events_to_out(session, [event], include_detail=True))[0]


@router.get("/products", response_model=ProductListOut)
async def list_products(session: AsyncSession = Depends(get_session)):
    from app.models import Product as ProductModel
    from app.services.views import product_to_out

    products = list(
        (await session.execute(select(ProductModel).order_by(ProductModel.id))).scalars()
    )
    items = [await product_to_out(session, p) for p in products]
    return ProductListOut(items=items)


@router.get("/products/{slug}", response_model=ProductOut)
async def get_product(slug: str, session: AsyncSession = Depends(get_session)):
    from fastapi import HTTPException

    from app.models import Product as ProductModel
    from app.services.views import product_to_out

    product = await session.scalar(
        select(ProductModel).where(ProductModel.slug == slug)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return await product_to_out(session, product)


@router.get("/site-config")
async def site_config(session: AsyncSession = Depends(get_session)):
    """公开站点配置：全服公告与文案区域（不含任何 SMTP 信息）。"""
    from app.services import settings as settings_service

    return {
        "announcement": await settings_service.get_announcement(session),
        "texts": await settings_service.get_texts(session),
    }


@router.get("/stats", response_model=PublicStats)
async def stats(session: AsyncSession = Depends(get_session)):
    by_platform_rows = await session.execute(
        select(RawContent.platform, func.count(RawContent.id)).group_by(RawContent.platform)
    )
    by_platform = {platform: count for platform, count in by_platform_rows}
    official = await session.scalar(
        select(func.count(RawContent.id)).join(Source, RawContent.source_id == Source.id).where(Source.identity == "official")
    )
    latest = await session.scalar(select(func.max(RawContent.collected_at)))
    total = await session.scalar(select(func.count(RawContent.id)))
    return PublicStats(
        total_contents=total or 0,
        by_platform=by_platform,
        official_contents=official or 0,
        latest_collected_at=latest,
    )
