"""公开 API：内容流、来源健康、统计。只暴露可公开的信息（无错误详情、无内部字段）。

公开页当前展示"原始内容流"；事件聚合视图属于事件引擎阶段（设计文档 §4）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.clock import utcnow
from app.models import CollectTarget, ContentVersion, Evidence, RawContent, Source
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


def _preview_mode(text: str | None, media: list, url: str | None) -> str:
    if text and text.strip():
        return "full"
    if media:
        return "partial"
    return "external_only"


@router.get("/contents", response_model=PublicContentList)
async def list_contents(
    platform: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    identity: str | None = Query(default=None, description="official/user/... 来源身份过滤"),
    exclude_identity: str | None = Query(default=None, max_length=32, description="排除指定来源身份"),
    published_within_hours: int | None = Query(
        default=None, ge=1, le=8760, description="仅返回最近若干小时发布的内容"
    ),
    include_chat: bool = Query(default=False, description="包含闲聊/杂谈类（默认隐藏降噪）"),
    sort: str = Query(default="time", pattern="^(time|hot)$", description="hot=按最新播放量排序"),
    q: str | None = Query(default=None, max_length=100, description="标题关键词"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    from app.services.classify import HIDDEN_CATEGORIES

    # 只关联"最新版本"，保证过滤与展示都基于当前分类
    latest_sq = (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("v"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )
    conditions = []
    if platform:
        conditions.append(RawContent.platform == platform)
    if content_type:
        conditions.append(RawContent.content_type == content_type)
    if identity:
        conditions.append(Source.identity == identity)
    if exclude_identity:
        conditions.append((Source.identity != exclude_identity) | Source.identity.is_(None))
    if published_within_hours:
        conditions.append(RawContent.published_at >= utcnow() - timedelta(hours=published_within_hours))
    if not include_chat:
        conditions.append(ContentVersion.category.notin_(HIDDEN_CATEGORIES) | ContentVersion.category.is_(None))
    if q:
        conditions.append(ContentVersion.title.like(f"%{q}%"))

    rows = await session.execute(
        select(RawContent, Source.name, Source.identity, ContentVersion)
        .join(latest_sq, latest_sq.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest_sq.c.rid)
            & (ContentVersion.version == latest_sq.c.v),
        )
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(*conditions)
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc())
        .limit(200 if sort == "hot" else limit)
        .offset(0 if sort == "hot" else offset)
    )
    candidates: list[PublicContentItem] = []
    seen: set[int] = set()
    for raw, source_name, source_identity, version in rows:
        if raw.id in seen:
            continue
        seen.add(raw.id)
        normalized = version.normalized_json if version else None
        text = (version.text if version else None) or str((normalized or {}).get("text") or "")
        media = (normalized or {}).get("media") or []
        candidates.append(
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
                category=version.category,
                metrics=None,
                cover=media[0] if isinstance(media, list) and media else None,
                preview_mode=_preview_mode(text, media if isinstance(media, list) else [], raw.url),
                matched_keywords=(raw.matched_keywords or []),
            )
        )

    metrics_map = await _latest_metrics(session, [i.id for i in candidates])
    for item in candidates:
        item.metrics = metrics_map.get(item.id)
    if sort == "hot":
        candidates.sort(
            key=lambda i: (i.metrics or {}).get("view", 0), reverse=True
        )
        items = candidates[offset : offset + limit]
    else:
        items = candidates

    count_sq = (
        select(func.count(func.distinct(RawContent.id)))
        .join(latest_sq, latest_sq.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest_sq.c.rid)
            & (ContentVersion.version == latest_sq.c.v),
        )
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(*conditions)
    )
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
    if "<" in text and ">" in text:
        import bleach

        text = bleach.clean(text, tags=[], strip=True).strip()
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

    from app.models import ContentTranslation
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

    translation = None
    translation_row = await session.get(ContentTranslation, content_id)
    if translation_row is not None:
        translation = {
            "lang": translation_row.lang,
            "text": translation_row.text,
            "model": translation_row.model,
        }

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
        translation=translation,
        matched_keywords=(raw.matched_keywords or []),
        version=version.version if version else None,
        preview_mode=_preview_mode(text, media, raw.url),
    )

@router.get("/events", response_model=EventListOut)
async def list_events(
    category: str | None = Query(default=None),
    product_slug: str | None = Query(default=None),
    importance: bool | None = Query(default=None),
    published_within_hours: int | None = Query(
        default=None, ge=1, le=8760, description="仅返回最近若干小时内有新证据的事件"
    ),
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
    if published_within_hours:
        cutoff = utcnow() - timedelta(hours=published_within_hours)
        recent_event_ids = (
            select(Evidence.event_id)
            .join(RawContent, RawContent.id == Evidence.raw_content_id)
            .where(RawContent.published_at >= cutoff)
        )
        conditions.append(EventModel.id.in_(recent_event_ids))
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
                .order_by(EventModel.importance.desc(), EventModel.first_seen_at.desc(), EventModel.id.desc())
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
