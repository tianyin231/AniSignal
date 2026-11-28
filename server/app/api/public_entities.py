"""公开事件、周边商品与官方动态外链聚合接口。"""

from datetime import timedelta
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.api.public import (
    COMMUNITY_AGGREGATE_QUERY,
    COMMUNITY_QUERY,
    EVENTS_CACHE_SECONDS,
    MAX_PUBLIC_OFFSET,
    PUBLIC_CACHE_SECONDS,
)
from app.clock import utcnow
from app.communities import CommunityOrAllSlug, CommunitySlug, community_conditions
from app.db import get_session
from app.models import ContentReview, ContentVersion, Evidence, RawContent, Source
from app.schemas.api import (
    EventListOut,
    EventOut,
    ProductListOut,
    ProductOut,
    ProductSignalListOut,
)
from app.services.hidden_communities import request_hidden
from app.services.public_cache import (
    CACHE_PREFIX,
    cached_model,
    official_links_cache as _official_links_cache,
)
from app.services.urls import safe_link

router = APIRouter(prefix="/api")

_EVENTS_CACHE_LIMITS = frozenset({6, 20, 30})


@router.get("/events", response_model=EventListOut)
async def list_events(
    request: Request,
    category: str | None = Query(default=None),
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    product_slug: str | None = Query(default=None),
    importance: bool | None = Query(default=None),
    published_within_hours: int | None = Query(
        default=None, ge=1, le=8760, description="仅返回最近若干小时内有新证据的事件"
    ),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=MAX_PUBLIC_OFFSET),
    session: AsyncSession = Depends(get_session),
):
    from app.models import Event as EventModel
    from app.models import Product as ProductModel
    from app.services.views import events_to_out

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = (
        f"{CACHE_PREFIX}events:{community}:{limit}"
        if (
            not hidden
            and category is None
            and product_slug is None
            and importance is None
            and published_within_hours is None
            and q is None
            and offset == 0
            and limit in _EVENTS_CACHE_LIMITS
        )
        else None
    )

    async def _load() -> EventListOut:
        # 资讯事件由专用发布层重新核对审核版本与引用，避免游戏出口暴露旧摘要。
        conditions = [EventModel.is_active.is_(True), EventModel.category != "ai_news",
                      *community_conditions(EventModel.community, community, hidden)]
        latest_evidence_sq = (
            select(
                Evidence.event_id.label("event_id"),
                func.max(
                    func.coalesce(RawContent.published_at, RawContent.collected_at)
                ).label("latest_evidence_at"),
            )
            .join(RawContent, RawContent.id == Evidence.raw_content_id)
            .where(Evidence.role != "excluded", RawContent.hidden.is_(False))
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
                    RawContent.hidden.is_(False),
                )
            )
            conditions.append(EventModel.id.in_(recent_event_ids))
        if q:
            from app.services.search import event_search_condition, supports_mysql_fts

            search_cond = event_search_condition(q, use_mysql_fts=await supports_mysql_fts(session))
            if search_cond is not None:
                conditions.append(search_cond)

        total = await session.scalar(
            select(func.count(EventModel.id))
            .join(latest_evidence_sq, latest_evidence_sq.c.event_id == EventModel.id)
            .where(*conditions)
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

    return await cached_model(cache, cache_key, EVENTS_CACHE_SECONDS, EventListOut, _load)


@router.get("/events/{event_id}", response_model=EventOut)
async def get_event(event_id: int, session: AsyncSession = Depends(get_session)):
    from app.models import Event as EventModel
    from app.services.views import events_to_out

    event = await session.get(EventModel, event_id)
    if event is None or not event.is_active or event.category == "ai_news":
        raise HTTPException(status_code=404, detail="event not found")
    return (await events_to_out(session, [event], include_detail=True))[0]


@router.get("/products", response_model=ProductListOut)
async def list_products(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    from app.models import Product as ProductModel
    from app.services.views import products_to_out

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}products:{community}" if not hidden else None

    async def _load() -> ProductListOut:
        query = select(ProductModel).where(
            *community_conditions(ProductModel.community, community, hidden)
        )
        products = list(
            (
                await session.execute(
                    query.order_by(ProductModel.id)
                )
            ).scalars()
        )
        items = await products_to_out(session, products)
        return ProductListOut(items=items)

    return await cached_model(cache, cache_key, PUBLIC_CACHE_SECONDS, ProductListOut, _load)


@router.get("/products/{slug}", response_model=ProductOut)
async def get_product(slug: str, session: AsyncSession = Depends(get_session)):
    from app.models import Product as ProductModel
    from app.services.views import product_to_out

    product = await session.scalar(
        select(ProductModel).where(ProductModel.slug == slug)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return await product_to_out(session, product)


@router.get("/products/{slug}/signals", response_model=ProductSignalListOut)
async def get_product_signals(
    slug: str, session: AsyncSession = Depends(get_session)
):
    from app.models import Product as ProductModel
    from app.services.fulfillment import list_product_signals

    product = await session.scalar(select(ProductModel).where(ProductModel.slug == slug))
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    items = await list_product_signals(session, product.id, verified_only=True, include_notes=False)
    return ProductSignalListOut(items=items, total=len(items))


# ---------- 官方动态外链聚合（活动/奖励/H5 直达） ----------

_OFFICIAL_LINKS_TTL_SECONDS = 300.0
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'）)\]}】」]+")
# 纯静态资源与站内图片不入链接清单
_LINK_HOST_BLOCKLIST = ("hdslb.com", "bilivideo", "ytimg.com", "ggpht.com", "static.", "akamaized.net")
_LINK_PATH_BLOCKLIST = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".css", ".js", ".json")
# 官方简介里的固定社媒/频道链接是"关注我们"模板，不是活动链接；
# 网盘与 UGC 作品页（二创作者主页、资源盘）同样不是官方公告的去处
_LINK_SOCIAL_HOSTS = (
    "facebook.com", "twitter.com", "x.com", "discord", "reddit.com", "tiktok",
    "instagram.com", "t.me", "whatsapp", "weibo.com", "youtube.com/c/",
    "youtube.com/channel/", "hoYoLAB".lower(),
    "pan.baidu.com", "pan.quark.cn", "pan.xunlei.com", "lanzou", "123pan",
    "aliyundrive", "alipan.com", "share.weiyun.com", "caiyun.139.com",
    "deviantart.com", "pixiv.net", "artstation.com",
)
# 模板链接判定：同一条 URL 在扫描窗口内出现 ≥3 次（下载页/官网首页每条视频都带）
_LINK_BOILERPLATE_MIN_HITS = 3
# 视频页面链接是内容互推（官方转发视频），不是活动/奖励/H5 页面
_LINK_VIDEO_PATTERNS = (
    "bilibili.com/video/", "m.bilibili.com/video/", "bilibili.com/opus/",
    "b23.tv/", "youtube.com/watch", "youtu.be/", "youtube.com/shorts/",
)
# 这些"标签"看不出链接是什么内容（常见于官网英文页锚文本/正文词），回退用来源内容标题
_LINK_GENERIC_LABELS = {
    "information", "info", "link", "url", "here", "详情", "查看详情", "点击查看",
    "阅读原文", "原文", "官网", "主页", "下载", "download", "news", "notice",
    "了解更多", "learn more", "read more", "view more", "点击进入", "点击前往",
}


def _usable_link_label(label: str | None, url: str) -> bool:
    text = (label or "").strip()
    if len(text) < 2:
        return False
    if text.lower() in _LINK_GENERIC_LABELS:
        return False
    host = url.split("/")[2].lower() if url.count("/") >= 2 else ""
    return not (host and text.lower() in host)


def _clean_url(value: str) -> str | None:
    url = value.rstrip('.,、;:!?"“”…—-，！？：；。）')
    if not url.startswith(("http://", "https://")):
        return None
    lowered = url.lower()
    if any(host in lowered for host in _LINK_HOST_BLOCKLIST):
        return None
    if any(host in lowered for host in _LINK_SOCIAL_HOSTS):
        return None
    if lowered.split("?")[0].endswith(_LINK_PATH_BLOCKLIST):
        return None
    if any(pattern in lowered for pattern in _LINK_VIDEO_PATTERNS):
        return None
    return url


def _link_category(url: str) -> str:
    host = url.split("/")[2].lower() if url.count("/") >= 2 else ""
    if "kuro" in host or "aki-game" in host:
        return "kuro"
    if "bilibili.com" in host or "bili2233" in host:
        return "bilibili"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "miyoushe" in host or "mihoyo" in host:
        return "miyoushe"
    return "web"


def _link_label(raw_text: str, clean_text: str, url: str) -> str:
    """链接的可读名称：优先锚文本，其次 URL 前最近的说明短语。"""
    def _tidy(value: str) -> str:
        value = re.sub(r"^[\W_]+|[\W_]+$", "", value.strip(), flags=re.UNICODE)
        return value if len(value) >= 4 else ""

    anchor = re.search(re.escape(url) + r'"[^>]*>([^<]{2,40})<', raw_text)
    if anchor:
        tidied = _tidy(anchor.group(1))
        if tidied:
            return tidied
    for text in (clean_text, raw_text):
        idx = text.find(url)
        if idx < 0:
            continue
        context = text[max(0, idx - 30):idx]
        # 中文无空格分词：按标点与空白切分，取 URL 前最近的说明短语
        segments = [seg for seg in re.split(r"[：:，,。.!！？?；;（）()【】\[\]\s]+", context) if seg]
        for seg in reversed(segments):
            tidied = _tidy(seg)
            if tidied:
                return tidied
    return ""


async def _collect_official_links(session: AsyncSession, community: str) -> list[dict]:
    import bleach
    from html import unescape

    publication_time = func.coalesce(RawContent.published_at, RawContent.collected_at)
    candidate_ids = list(
        await session.scalars(
            select(RawContent.id)
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .join(ContentReview, ContentReview.raw_content_id == RawContent.id, isouter=True)
            .where(
                Source.identity == "official",
                RawContent.hidden.is_(False),
                RawContent.community == community,
                ContentReview.links.is_(None),  # AI 已提取过的内容不再走规则回退
                publication_time >= utcnow() - timedelta(days=30),
            )
            .order_by(publication_time.desc(), RawContent.id.desc())
            .limit(300)
        )
    )
    fetched = {
        row[0].id: row
        for row in (
            await session.execute(
                select(RawContent, ContentVersion, Source)
                .options(defer(RawContent.raw_json), defer(Source.raw_json))
                .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                .join(Source, RawContent.source_id == Source.id, isouter=True)
                .where(RawContent.id.in_(candidate_ids))
            )
        ).all()
    } if candidate_ids else {}
    rows = [fetched[cid] for cid in candidate_ids if cid in fetched]
    occurrences: dict[str, list[tuple[RawContent, ContentVersion, str]]] = {}
    for raw, version, source in rows:
        raw_text = version.text or str((version.normalized_json or {}).get("text") or "")
        if "http" not in raw_text:
            continue
        clean_text = re.sub(r"\s+", " ", bleach.clean(unescape(raw_text), tags=[], strip=True))
        raw_url = raw.url or ""
        for match in _URL_PATTERN.finditer(raw_text):
            url = _clean_url(match.group(0))
            if not url or url == raw_url:
                continue
            occurrences.setdefault(url, []).append(
                (raw, version, _link_label(raw_text, clean_text, url))
            )

    items: list[dict] = []
    for url, hits in occurrences.items():
        # 出现在大量不同官方内容里的固定链接（下载页/官网/社媒块）不是活动链接
        if len(hits) >= _LINK_BOILERPLATE_MIN_HITS:
            continue
        raw, version, label = hits[0]
        title = (version.title or raw.external_id)[:80]
        safe = safe_link(url)
        if not safe:
            continue  # 非 http(s)/站内路径：不进链接榜
        url = safe
        items.append(
            {
                "url": url,
                "category": _link_category(url),
                "label": (label if _usable_link_label(label, url) else "") or title[:40],
                "title": title,
                "platform": raw.platform,
                "published_at": raw.published_at or raw.collected_at,
            }
        )
        if len(items) >= 30:
            break
    return items


async def _ai_official_links(session: AsyncSession, community: str) -> list[dict]:
    """AI 审核提取的外链（已带简体中文名称）——**仅限官方身份内容**。

    用户/媒体内容的 AI 提取链接（网盘、二创作者主页等）不属于"官方动态的链接"，
    一律不进面板；标签不可读（如官网英文页的 "information"）时回退来源内容标题。
    """
    publication_time = func.coalesce(RawContent.published_at, RawContent.collected_at)
    candidate_ids = list(
        await session.scalars(
            select(RawContent.id)
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .join(ContentReview, ContentReview.raw_content_id == RawContent.id)
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(
                Source.identity == "official",
                ContentReview.links.is_not(None),
                RawContent.hidden.is_(False),
                RawContent.community == community,
                publication_time >= utcnow() - timedelta(days=30),
            )
            .order_by(publication_time.desc(), RawContent.id.desc())
            .limit(300)
        )
    )
    fetched = {
        row[0].id: row
        for row in (
            await session.execute(
                select(RawContent, ContentReview, ContentVersion)
                .options(
                    defer(RawContent.raw_json),
                    defer(ContentVersion.text),
                    defer(ContentVersion.normalized_json),
                    defer(ContentReview.fulfillment),
                    defer(ContentReview.news),
                )
                .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                .join(ContentReview, ContentReview.raw_content_id == RawContent.id)
                .where(RawContent.id.in_(candidate_ids))
            )
        ).all()
    } if candidate_ids else {}
    rows = [fetched[cid] for cid in candidate_ids if cid in fetched]
    items: list[dict] = []
    seen: set[str] = set()
    from html import unescape

    for raw, review, version in rows:
        title = (version.title or raw.external_id)[:80]
        for link in review.links or []:
            # 历史数据可能存有 HTML 实体（&amp;），setAttribute 不解码，必须还原
            url = _clean_url(unescape(str(link.get("url") or "")))
            if not url or url in seen:
                continue
            # links 来自 AI 抽取，可能带 javascript:/data: 等形态，统一收敛后再下发
            url = safe_link(url)
            if not url:
                continue
            seen.add(url)
            label = str(link.get("label") or "").strip()
            if not _usable_link_label(label, url):
                label = title[:40] or url
            items.append(
                {
                    "url": url,
                    "category": _link_category(url),
                    "label": label[:80],
                    "title": title,
                    "platform": raw.platform,
                    "published_at": raw.published_at or raw.collected_at,
                }
            )
    return items


@router.get("/official-links")
async def official_links(
    community: CommunitySlug = COMMUNITY_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """官方动态中指向活动/奖励/H5 的站外链接，去重去模板后按最新排序。

    优先用 AI 审核提取的链接（带简体中文名称）；尚未审核的内容退回规则提取。
    """
    now = time.monotonic()
    cached = _official_links_cache.get(community)
    if cached and cached["data"] is not None and now < cached["expires"]:
        return {"items": cached["data"]}
    items = await _ai_official_links(session, community)
    seen = {item["url"] for item in items}
    for item in await _collect_official_links(session, community):
        if item["url"] not in seen:
            seen.add(item["url"])
            items.append(item)
        if len(items) >= 30:
            break
    _official_links_cache[community] = {
        "data": items,
        "expires": now + _OFFICIAL_LINKS_TTL_SECONDS,
    }
    return {"items": items}
