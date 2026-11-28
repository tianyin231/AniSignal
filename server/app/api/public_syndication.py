"""公开 SEO 与订阅分发接口（robots.txt、sitemap.xml、公共 RSS 与个人专属过滤 RSS）。"""

from datetime import datetime, timezone
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.api.public import (
    COMMUNITY_AGGREGATE_QUERY,
    _preview_mode,
    list_contents,
    public_site_base,
)
from app.communities import COMMUNITIES, CommunityOrAllSlug, DEFAULT_COMMUNITY, is_all_community
from app.db import get_session
from app.models import ContentVersion, RawContent, Source
from app.schemas.api import PublicContentItem
from app.services.public_cache import sitemap_cache as _SITEMAP_CACHE

router = APIRouter(prefix="/api")

_RSS_CACHE: dict[str, tuple[float, str]] = {}
_RSS_TTL_SECONDS = 300

_SITEMAP_TTL_SECONDS = 3600
_SITEMAP_PAGES = (
    "/", "/ai", "/ai/reports", "/ai/sources", "/official", "/calendar", "/events", "/products", "/steam", "/contents",
    "/sources", "/stats", "/about", "/intro", "/project",
)


@router.get("/robots.txt", response_class=Response)
async def robots_txt(request: Request):
    base = public_site_base(request)
    content = "\n".join(
        [
            "# AniSignal robots.txt",
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin",
            "Disallow: /account",
            "Disallow: /api/",
            "Disallow: /login",
            "Disallow: /register",
            "Disallow: /forgot",
            "Crawl-delay: 10",
            f"Sitemap: {base}/sitemap.xml",
            "",
        ]
    )
    return Response(content=content, media_type="text/plain; charset=utf-8")


@router.get("/sitemap.xml", response_class=Response)
async def sitemap(request: Request, session: AsyncSession = Depends(get_session)):
    """站点地图：公开栏目、游戏频道、全部商品和进行中的事件。"""
    from xml.sax.saxutils import escape

    from app.models import Event as EventModel
    from app.models import Product as ProductModel

    base = public_site_base(request)
    cached = _SITEMAP_CACHE.get(base)
    if cached and cached[0] > time.time():
        return Response(content=cached[1], media_type="application/xml; charset=utf-8")

    entries: list[tuple[str, datetime | None]] = [(path, None) for path in _SITEMAP_PAGES]
    entries.extend((f"/?community={key}", None) for key, meta in COMMUNITIES.items() if meta.domain == "game")
    entries.extend((f"/contents?community={key}", None) for key, meta in COMMUNITIES.items() if meta.active and meta.domain == "feed")
    products = await session.execute(select(ProductModel.slug, ProductModel.community, ProductModel.fulfillment_updated_at))
    entries.extend((f"/products/{slug}?community={community}", updated) for slug, community, updated in products)
    events = await session.execute(
        select(EventModel.id, EventModel.last_updated_at)
        .where(EventModel.is_active.is_(True), EventModel.category != "ai_news")
        .order_by(EventModel.last_updated_at.desc())
        .limit(5000)
    )
    entries.extend((f"/events/{event_id}", updated) for event_id, updated in events)

    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, updated in entries:
        lastmod = f"<lastmod>{updated:%Y-%m-%d}</lastmod>" if updated else ""
        parts.append(f"<url><loc>{escape(base + path)}</loc>{lastmod}</url>")
    parts.append("</urlset>")
    xml = "\n".join(parts)
    _SITEMAP_CACHE[base] = (time.time() + _SITEMAP_TTL_SECONDS, xml)
    return Response(content=xml, media_type="application/xml; charset=utf-8")


def _render_rss_xml(
    items: list[PublicContentItem],
    *,
    title: str,
    link: str,
    description: str,
    base: str,
    revisions_mode: bool = False,
) -> str:
    from email.utils import format_datetime
    from xml.sax.saxutils import escape

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{escape(title)}</title>",
        f"<link>{escape(link)}</link>",
        f"<description>{escape(description)}</description>",
        "<generator>AniSignal</generator>",
    ]
    for item in items:
        published = item.published_at or item.collected_at
        pub_date = format_datetime(published.replace(tzinfo=timezone.utc))
        item_link = f"{base}/contents/{item.id}#revisions" if revisions_mode else (item.url or f"{base}/contents/{item.id}")
        guid = (
            f"anisignal-content-{item.id}-rev-{item.revision_count}"
            if revisions_mode and item.revision_count
            else f"anisignal-content-{item.id}"
        )
        parts.extend(
            [
                "<item>",
                f"<title>{escape(item.title or item.external_id)}</title>",
                f"<link>{escape(item_link)}</link>",
                f"<guid>{escape(guid)}</guid>",
                f"<pubDate>{pub_date}</pubDate>",
                f"<description>{escape(item.text_excerpt or item.title or '')}</description>",
            ]
        )
        if item.cover:
            parts.append(
                f'<enclosure url="{escape(item.cover)}" type="image/jpeg" length="0"/>'
            )
        parts.append("</item>")
    parts.extend(["</channel>", "</rss>"])
    return "\n".join(parts)


@router.get("/rss.xml", response_class=Response)
async def rss_feed(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    q: str | None = Query(default=None, max_length=100),
    revised_only: bool = Query(default=False),
    limit: int = Query(default=30, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """RSS 2.0 订阅源：默认最新内容流，可选 revised_only=true 仅输出实质修订流，5 分钟进程内缓存。"""
    only_revised = revised_only is True
    cache_key = f"{community}|{q or ''}|{int(only_revised)}|{limit}"
    cached = _RSS_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return Response(content=cached[1], media_type="application/rss+xml; charset=utf-8")

    # 直接调用 endpoint 函数时 FastAPI 的 Query 默认值不会解析，必须显式传全部参数
    # RSS 结果在进程内共享缓存，不能带上某个账号的屏蔽：request 传 None。
    listing = await list_contents(
        request=None,
        platform=None,
        community=community,
        content_type=None,
        identity="official" if only_revised else None,
        exclude_identity=None,
        published_within_hours=None,
        include_chat=False,
        revised_only=only_revised,
        sort="time",
        q=q,
        topic=None,
        limit=limit,
        offset=0,
        session=session,
    )
    base = public_site_base(request)
    label = "跨分区聚合" if is_all_community(community) else community
    rss_title = f"AniSignal 官方修订流 · {label}" if only_revised else f"AniSignal 情报流 · {label}"
    rss_desc = "官方公告与动态实质修订追踪（抓暗改）" if only_revised else "游戏动态与商品履约追踪"
    xml = _render_rss_xml(
        listing.items,
        title=rss_title,
        link=f"{base}/official" if only_revised else base,
        description=rss_desc,
        base=base,
        revisions_mode=only_revised,
    )
    _RSS_CACHE[cache_key] = (time.time() + _RSS_TTL_SECONDS, xml)
    if len(_RSS_CACHE) > 32:
        for stale_key in [k for k, v in _RSS_CACHE.items() if v[0] <= time.time()]:
            _RSS_CACHE.pop(stale_key, None)
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/rss/personal/{token}.xml", response_class=Response)
async def personal_rss_feed(
    token: str,
    request: Request,
    mode: str = Query(default="all", pattern="^(all|keywords|revisions)$"),
    limit: int = Query(default=30, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """个人专属过滤 RSS：按账号的分区偏好、屏蔽词/屏蔽分区、关注词或官方修订实时过滤。"""
    from html import unescape

    from app.models import User, UserKeyword
    from app.services import settings as settings_service
    from app.services.classify import HIDDEN_CATEGORIES
    from app.services.feed_preferences import preference_conditions
    from app.services.hidden_communities import account_blocks

    cleaned_token = token.strip()
    if not cleaned_token or len(cleaned_token) > 64:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    user = await session.scalar(
        select(User).where(User.feed_token == cleaned_token, User.status == "active")
    )
    if user is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")

    base = public_site_base(request)
    prefs = user.feed_preferences or {}
    conditions: list = [
        RawContent.hidden.is_(False),
        ContentVersion.category.notin_(HIDDEN_CATEGORIES) | ContentVersion.category.is_(None),
        *preference_conditions(prefs),
    ]
    blocks = account_blocks(user)
    # 仅在用户未显式限定「只看这些分区」时应用网站屏蔽分区，防止矛盾过滤。
    if not prefs.get("communities") and blocks.communities:
        conditions.append(RawContent.community.notin_(blocks.communities))
    conditions.extend(blocks.platform_conditions(RawContent.platform, RawContent.community))

    hidden_platforms = settings_service.hidden_platforms(
        await settings_service.get_media_visibility(session)
    )
    if hidden_platforms:
        conditions.append(RawContent.platform.notin_(hidden_platforms))

    if mode == "keywords":
        keywords = list(
            await session.scalars(
                select(UserKeyword)
                .where(UserKeyword.user_id == user.id, UserKeyword.enabled.is_(True))
                .order_by(UserKeyword.id)
            )
        )
        if not keywords:
            xml = _render_rss_xml(
                [],
                title="AniSignal 个人关注流（关键词）",
                link=f"{base}/account",
                description="暂未启用关注关键词",
                base=base,
            )
            return Response(
                content=xml,
                media_type="application/rss+xml; charset=utf-8",
                headers={"Cache-Control": "private, max-age=120"},
            )
        conditions.append(
            or_(
                *[
                    func.lower(func.coalesce(ContentVersion.title, "")).contains(
                        k.keyword.lower(), autoescape=True
                    )
                    for k in keywords
                ]
            )
        )

    if mode == "revisions":
        from app.services.revision_notifications import load_official_revision_events

        allowed_comms = prefs.get("communities") or [
            key for key in COMMUNITIES if key not in blocks.communities
        ]
        rev_events, _ = await load_official_revision_events(
            session,
            communities=allowed_comms,
            exclude_keywords=prefs.get("exclude_keywords") or None,
            limit=max(limit * 3, 60),
        )
        rev_items: list[PublicContentItem] = []
        for ev in rev_events:
            if hidden_platforms and ev["platform"] in hidden_platforms:
                continue
            if blocks.platforms:
                comm_meta = COMMUNITIES.get(ev["community"])
                domain = comm_meta.domain if comm_meta else "game"
                if f"{domain}:{ev['platform']}" in blocks.platforms:
                    continue
            change_head = f"v{ev['from_version']}→v{ev['to_version']}（{ev['change_label']}）"
            body_lines = [change_head, *(ev.get("change_lines") or [])]
            rev_items.append(
                PublicContentItem(
                    id=ev["content_id"],
                    platform=ev["platform"],
                    content_type=ev["content_type"],
                    external_id=str(ev["content_id"]),
                    url=ev["detail_url"],
                    title=f"[修订] {ev['title']}",
                    text_excerpt="\n".join(body_lines)[:400],
                    cover=ev.get("image"),
                    source_name=ev["source"],
                    identity="official",
                    published_at=ev["parsed_at"],
                    collected_at=ev["parsed_at"],
                    metrics=None,
                    matched_keywords=[],
                    category="official_revision",
                    community=ev["community"],
                    preview_mode="full",
                    revision_count=ev["to_version"] - 1,
                    has_source_revision=True,
                )
            )
            if len(rev_items) >= limit:
                break
        xml = _render_rss_xml(
            rev_items,
            title="AniSignal 个人订阅 · 官方修订",
            link=f"{base}/official",
            description="按个人偏好过滤的官方公告实质修订追踪流",
            base=base,
            revisions_mode=True,
        )
        return Response(
            content=xml,
            media_type="application/rss+xml; charset=utf-8",
            headers={"Cache-Control": "private, max-age=120"},
        )

    page_ids = list(
        await session.scalars(
            select(RawContent.id)
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .outerjoin(Source, Source.id == RawContent.source_id)
            .where(*conditions)
            .order_by(func.coalesce(RawContent.published_at, RawContent.collected_at).desc(), RawContent.id.desc())
            .limit(limit)
        )
    )
    fetched_rows = {
        row[0].id: row
        for row in (
            await session.execute(
                select(RawContent, Source, ContentVersion)
                .options(defer(RawContent.raw_json), defer(Source.raw_json))
                .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                .outerjoin(Source, Source.id == RawContent.source_id)
                .where(RawContent.id.in_(page_ids))
            )
        ).all()
    } if page_ids else {}
    rows = [fetched_rows[cid] for cid in page_ids if cid in fetched_rows]
    items: list[PublicContentItem] = []
    for raw, source, version in rows:
        normalized = (version.normalized_json if version else None) or {}
        media = normalized.get("media") or []
        text_body = (version.text if version else None) or str(normalized.get("text") or "")
        excerpt = text_body.strip()
        if "<" in excerpt and ">" in excerpt:
            excerpt = re.sub(r"<[^>]+>", "", unescape(excerpt))
        items.append(
            PublicContentItem(
                id=raw.id,
                platform=raw.platform,
                content_type=raw.content_type,
                external_id=raw.external_id,
                url=raw.url,
                title=(version.title if version else None) or raw.external_id,
                text_excerpt=excerpt[:220] if excerpt else None,
                cover=media[0] if media and isinstance(media[0], str) else None,
                source_name=source.name if source else None,
                source_identity=source.identity if source else None,
                identity=source.identity if source else "unknown",
                published_at=raw.published_at,
                collected_at=raw.collected_at,
                metrics=None,
                matched_keywords=raw.matched_keywords or [],
                category=version.category if version else None,
                community=getattr(raw, "community", None) or DEFAULT_COMMUNITY,
                preview_mode=_preview_mode(text_body, media, raw.url),
            )
        )

    mode_label = "关键词命中" if mode == "keywords" else "个人偏好过滤"
    xml = _render_rss_xml(
        items,
        title=f"AniSignal 个人订阅 · {mode_label}",
        link=f"{base}/account",
        description=f"按个人偏好与降噪规则过滤的专属情报源（{mode_label}）",
        base=base,
    )
    return Response(
        content=xml,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "private, max-age=120"},
    )
