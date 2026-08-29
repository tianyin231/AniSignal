"""公开 API：内容流、来源健康、统计。只暴露可公开的信息（无错误详情、无内部字段）。

公开页当前展示"原始内容流"；事件聚合视图属于事件引擎阶段（设计文档 §4）。
"""

import asyncio
import hashlib
import io
import logging
import os
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from PIL import Image as PILImage
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
    StatisticsOverview,
)

router = APIRouter(prefix="/api")

_EXCERPT_LEN = 140


@router.get("/highlights", response_model=PublicContentList)
async def list_highlights(
    limit: int = Query(default=5, ge=1, le=8),
    session: AsyncSession = Depends(get_session),
):
    """首页精选：官方实质动态与高热度社区内容混排。"""
    from app.services.curation import select_highlights

    items = [PublicContentItem(**item) for item in await select_highlights(session, limit)]
    return PublicContentList(items=items, total=len(items))


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
        conditions.append(
            RawContent.platform.in_(("kuro_cn", "kuro_global"))
            if platform == "kuro"
            else RawContent.platform == platform
        )
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

    publication_time = func.coalesce(RawContent.published_at, RawContent.collected_at)
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
        .order_by(publication_time.desc(), RawContent.id.desc())
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
    latest_sq = (
        select(
            MetricSnapshot.raw_content_id.label("raw_content_id"),
            func.max(MetricSnapshot.id).label("snapshot_id"),
        )
        .where(MetricSnapshot.raw_content_id.in_(content_ids))
        .group_by(MetricSnapshot.raw_content_id)
        .subquery()
    )
    rows = await session.execute(
        select(MetricSnapshot).join(
            latest_sq, MetricSnapshot.id == latest_sq.c.snapshot_id
        )
    )
    return {snapshot.raw_content_id: snapshot.metrics for snapshot in rows.scalars()}


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
    latest_evidence_sq = (
        select(
            Evidence.event_id.label("event_id"),
            func.max(
                func.coalesce(RawContent.published_at, RawContent.collected_at)
            ).label("latest_evidence_at"),
        )
        .join(RawContent, RawContent.id == Evidence.raw_content_id)
        .where(Evidence.role != "excluded")
        .group_by(Evidence.event_id)
        .subquery()
    )
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
            .where(
                RawContent.published_at >= cutoff,
                Evidence.role != "excluded",
            )
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
                .join(latest_evidence_sq, latest_evidence_sq.c.event_id == EventModel.id)
                .where(*conditions)
                .order_by(
                    latest_evidence_sq.c.latest_evidence_at.desc(),
                    EventModel.importance.desc(),
                    EventModel.id.desc(),
                )
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


@router.get("/statistics", response_model=StatisticsOverview)
async def statistics(
    request: Request,
    days: int = Query(default=30),
    session: AsyncSession = Depends(get_session),
):
    """独立统计页数据；图表只读取预聚合表。"""
    if days not in {7, 30, 90, 365}:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="days must be one of 7, 30, 90, 365")

    cache = getattr(request.app.state, "redis", None)
    cache_key = f"statistics:v1:{days}"
    if cache is not None:
        try:
            cached = await cache.get(cache_key)
            if cached:
                return StatisticsOverview.model_validate_json(cached)
        except Exception:  # noqa: BLE001 - Redis 不可用时降级读聚合表
            pass

    from app.services.statistics import build_statistics

    result = await build_statistics(session, days)
    if cache is not None:
        try:
            await cache.set(cache_key, result.model_dump_json(), ex=60)
        except Exception:  # noqa: BLE001
            pass
    return result

# ---------- 随机壁纸背景（用户可关，失败回退默认背景）----------
#
# 加速策略：原图（2~8MB）只在服务端拉一次，转成 ~1920px WebP（几百 KB）+ 32px 模糊预览，
# 磁盘缓存后以长 Cache-Control 下发；前端先出预览再流入高清图，下载仍取原图。

WALLPAPER_API = "https://api.yppp.net/api.php"
# 同一解析结果最短复用时间：防手速刷新打爆源站，同时保证"每次刷新基本换图"
WALLPAPER_MIN_INTERVAL_SECONDS = 2.0
# 签发记录保留时长：只有服务端签发过的地址才允许取图/下载（防止变成开放代理）
WALLPAPER_ISSUED_TTL_SECONDS = 900.0
WALLPAPER_MAX_EDGE = 1920
WALLPAPER_QUALITY = 78
WALLPAPER_CACHE_DIR = os.environ.get("WALLPAPER_CACHE_DIR", "/tmp/anisignal-wallpaper")
WALLPAPER_TRANSFORM_TIMEOUT_SECONDS = 90.0

_wallpaper_last_resolve = 0.0
_wallpaper_last_url: str | None = None
_wallpaper_issued: dict[str, float] = {}
_wallpaper_lock = asyncio.Lock()
# 地址 -> 转换任务 Future（完成后磁盘缓存可用；失败时移除以便重试）
_wallpaper_transforms: dict[str, asyncio.Future] = {}

logger = logging.getLogger(__name__)


def _prune_issued(now: float) -> None:
    expired = [u for u, ts in _wallpaper_issued.items() if now - ts > WALLPAPER_ISSUED_TTL_SECONDS]
    for u in expired:
        _wallpaper_issued.pop(u, None)
        _wallpaper_transforms.pop(u, None)


def _ensure_issued(url: str) -> None:
    now = time.monotonic()
    _prune_issued(now)
    issued_at = _wallpaper_issued.get(url)
    if issued_at is None or now - issued_at > WALLPAPER_ISSUED_TTL_SECONDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "壁纸地址已过期，请刷新页面后重试")


def _cache_base(url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return os.path.join(WALLPAPER_CACHE_DIR, digest)


def _transform_sync(raw: bytes, base: str) -> None:
    """原图 -> 限定边长的高清 WebP + 32px 模糊预览（CPU 密集，放线程池跑）。"""
    with PILImage.open(io.BytesIO(raw)) as img:
        img = PILImage.exif_transpose(img).convert("RGB")
        img.thumbnail((WALLPAPER_MAX_EDGE, WALLPAPER_MAX_EDGE))
        os.makedirs(WALLPAPER_CACHE_DIR, exist_ok=True)
        img.save(f"{base}.webp", "WEBP", quality=WALLPAPER_QUALITY, method=4)
        thumb = img.copy()
        thumb.thumbnail((32, 32))
        thumb.save(f"{base}.preview.webp", "WEBP", quality=50, method=4)


async def _run_transform(url: str, fut: asyncio.Future) -> None:
    try:
        base = _cache_base(url)
        if not (os.path.exists(f"{base}.webp") and os.path.exists(f"{base}.preview.webp")):
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=httpx.Timeout(60.0)
            ) as client:
                resp = await client.get(url, headers={"User-Agent": "AniSignal/1.0 (wallpaper)"})
            if resp.status_code != 200:
                raise RuntimeError(f"origin status {resp.status_code}")
            await asyncio.to_thread(_transform_sync, resp.content, base)
        if not fut.done():
            fut.set_result(None)
    except Exception as exc:  # noqa: BLE001 - 失败后移除记录，下次请求重试
        logger.warning("wallpaper transform failed: %s", exc)
        if _wallpaper_transforms.get(url) is fut:
            _wallpaper_transforms.pop(url, None)
        if not fut.done():
            fut.set_exception(exc)


async def _await_transform(url: str) -> None:
    fut = _wallpaper_transforms.get(url)
    if fut is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "壁纸地址已过期，请刷新页面后重试")
    try:
        await asyncio.wait_for(asyncio.shield(fut), timeout=WALLPAPER_TRANSFORM_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "壁纸处理超时，请稍后重试") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "壁纸源暂时不可用") from None


async def _resolve_wallpaper() -> str | None:
    """跟随重定向拿到最终图片地址；只接受 http(s) 的图片响应。

    不做长缓存：每次页面加载都解析新图（刷新即换壁纸），仅用短间隔去重防抖。
    """
    global _wallpaper_last_resolve, _wallpaper_last_url
    async with _wallpaper_lock:
        now = time.monotonic()
        if (
            _wallpaper_last_url
            and now - _wallpaper_last_resolve < WALLPAPER_MIN_INTERVAL_SECONDS
        ):
            return _wallpaper_last_url
        try:
            headers = {"User-Agent": "AniSignal/1.0 (wallpaper)"}
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=httpx.Timeout(10.0)
            ) as client:
                request = client.build_request("GET", WALLPAPER_API, headers=headers)
                # 流式取响应头即够；原图体不读到本地，立刻释放
                resp = await client.send(request, stream=True)
                final_url = str(resp.url)
                content_type = resp.headers.get("content-type", "").lower()
                await resp.aclose()
            if resp.status_code != 200 or not final_url.startswith(("http://", "https://")):
                return None
            if not content_type.startswith("image/"):
                return None
        except Exception:  # noqa: BLE001 - 壁纸源故障只降级，不影响主功能
            logger.warning("wallpaper resolve failed", exc_info=True)
            return None
        _wallpaper_last_url = final_url
        _wallpaper_last_resolve = now
        _wallpaper_issued[final_url] = now
        _prune_issued(now)
        return final_url


async def _fetch_wallpaper_bytes(url: str) -> tuple[bytes, str]:
    """下载指定壁纸原图，返回 (内容, content_type)。只允许服务端签发过的地址。"""
    _ensure_issued(url)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(60.0)
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "AniSignal/1.0 (wallpaper)"})
    except Exception:  # noqa: BLE001
        logger.warning("wallpaper download failed", exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "壁纸源暂时不可用") from None
    content_type = resp.headers.get("content-type", "").lower()
    if resp.status_code != 200 or not content_type.startswith("image/"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "壁纸源暂时不可用")
    return resp.content, content_type


@router.get("/wallpaper")
async def wallpaper_info():
    """随机壁纸：返回展示/预览/原图三个地址（每次调用基本不同）。

    前端先出 1KB 级模糊预览，高清压缩图随后流入；下载仍取原图，
    服务端只转发自己签发过的地址，保证下载的就是当前展示的那张。
    """
    url = await _resolve_wallpaper()
    if url is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "壁纸源暂时不可用")
    fut = _wallpaper_transforms.get(url)
    if fut is None:
        fut = asyncio.get_running_loop().create_future()
        _wallpaper_transforms[url] = fut
        asyncio.create_task(_run_transform(url, fut))
    quoted = quote(url, safe="")
    return {
        "url": url,
        "image": f"/api/wallpaper/image?u={quoted}",
        "preview": f"/api/wallpaper/preview?u={quoted}",
    }


async def _wallpaper_cache_response(url: str, suffix: str, media_type: str) -> Response:
    _ensure_issued(url)
    await _await_transform(url)
    path = f"{_cache_base(url)}{suffix}"
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "壁纸源暂时不可用")
    with open(path, "rb") as fh:
        content = fh.read()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/wallpaper/image")
async def wallpaper_image(u: str = Query(...)):
    """当前壁纸的高清压缩版（~1920px WebP）。"""
    return await _wallpaper_cache_response(u, ".webp", "image/webp")


@router.get("/wallpaper/preview")
async def wallpaper_preview(u: str = Query(...)):
    """当前壁纸的 32px 模糊预览（~1KB，先于高清图送达）。"""
    return await _wallpaper_cache_response(u, ".preview.webp", "image/webp")


@router.get("/wallpaper/download")
async def wallpaper_download(url: str = Query(...)):
    """下载当前展示的背景原图（服务端流式转发，带附件下载头）。"""
    content, content_type = await _fetch_wallpaper_bytes(url)
    ext = (
        ".webp"
        if "webp" in content_type
        else ".png"
        if "png" in content_type
        else ".jpg"
    )
    filename = f"anisignal-wallpaper-{date.today():%Y%m%d}{ext}"
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
