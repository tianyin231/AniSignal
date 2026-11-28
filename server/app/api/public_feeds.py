"""公开主题流总览、GitHub Trending 刊物与 Steam 实时在线趋势接口。"""

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.api.public import (
    COMMUNITY_AGGREGATE_QUERY,
    PUBLIC_CACHE_SECONDS,
    _excerpt,
    _latest_metrics,
    _merge_topics,
)
from app.clock import utcnow
from app.communities import COMMUNITIES, CommunityOrAllSlug, CommunitySlug
from app.db import get_session
from app.models import CollectTarget, ContentVersion, RawContent, Source
from app.services.hidden_communities import request_blocks
from app.services.public_cache import CACHE_PREFIX, cached_dict
from app.services.urls import safe_link, safe_media_url

router = APIRouter(prefix="/api")

_STEAM_CACHE_HOURS = frozenset({24, 72, 168, 720})


@router.get("/feeds/overview")
async def feed_overview(
    request: Request,
    community: CommunitySlug = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """独立主题流信号台总览（小黑盒 / 开源 / 自建 feed 分区）：头图焦点、近期热门、话题分布与来源健康。"""
    from app.models import ContentBrief
    from app.services.content_brief import load_briefs_map
    from app.services.xiaoheihe import XIAOHEIHE_KEY, xiaoheihe_feed_status

    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}feed-overview:{community}"

    async def _load() -> dict:
        now = utcnow()
        pub_ts = func.coalesce(RawContent.published_at, RawContent.collected_at)
        total_count = await session.scalar(
            select(func.count(RawContent.id)).where(
                RawContent.community == community,
                RawContent.hidden.is_(False),
            )
        ) or 0
        recent_24h = await session.scalar(
            select(func.count(RawContent.id)).where(
                RawContent.community == community,
                RawContent.hidden.is_(False),
                RawContent.collected_at >= now - timedelta(hours=24),
            )
        ) or 0
        brief_count = await session.scalar(
            select(func.count(ContentBrief.raw_content_id))
            .join(RawContent, RawContent.id == ContentBrief.raw_content_id)
            .where(RawContent.community == community, RawContent.hidden.is_(False))
        ) or 0

        ids = list(
            await session.scalars(
                select(RawContent.id)
                .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                .where(RawContent.community == community, RawContent.hidden.is_(False))
                .order_by(pub_ts.desc(), RawContent.id.desc())
                .limit(120)
            )
        )
        fetched = {
            row[0].id: row
            for row in (
                await session.execute(
                    select(RawContent, Source.name, ContentVersion)
                    .options(defer(RawContent.raw_json))
                    .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                    .outerjoin(Source, Source.id == RawContent.source_id)
                    .where(RawContent.id.in_(ids))
                )
            ).all()
        } if ids else {}
        rows = [fetched[cid] for cid in ids if cid in fetched]

        metrics_map = await _latest_metrics(session, ids)
        briefs_map = await load_briefs_map(session, ids)

        topic_counter: Counter[str] = Counter()
        scored: list[tuple[int, dict]] = []
        for raw, source_name, ver in rows:
            norm = ver.normalized_json or {}
            extra = norm.get("extra") or {}
            raw_media = norm.get("media") or []
            media = [m for m in raw_media if isinstance(m, str)] if isinstance(raw_media, list) else []
            brief = briefs_map.get(raw.id)
            item_topics = _merge_topics(extra, brief, limit=12)
            for t in item_topics:
                topic_counter[t] += 1

            m = metrics_map.get(raw.id) or {}
            if raw.content_type == "repo":
                score = int(m.get("view") or 0) * 10 + int(m.get("like") or 0)
            else:
                score = int(m.get("like") or 0) * 3 + int(m.get("reply") or 0) * 2 + int(m.get("view") or 0)
            scored.append(
                (
                    score,
                    {
                        "id": raw.id,
                        "platform": raw.platform,
                        "content_type": raw.content_type,
                        "title": (ver.title if ver else None) or raw.external_id,
                        "excerpt": _excerpt((ver.text if ver else None) or ""),
                        "brief": brief,
                        "cover": safe_media_url(media[0]) if media else None,
                        "media_count": len(media),
                        "source_name": source_name,
                        "published_at": raw.published_at,
                        "collected_at": raw.collected_at,
                        "metrics": m or None,
                        "topics": item_topics[:4],
                        "url": safe_link(raw.url),
                    },
                )
            )

        hot_sorted = sorted(scored, key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
        hot_items = [item for _, item in hot_sorted[:5]]

        # 头图焦点：优先带封面的高互动内容，其次带 AI 解读的热门内容，最后取首条
        lead = (
            next((item for _, item in hot_sorted if item.get("cover")), None)
            or next((item for _, item in hot_sorted if item.get("brief")), None)
            or (hot_items[0] if hot_items else None)
        )

        target_rows = list(
            await session.scalars(
                select(CollectTarget)
                .where(CollectTarget.community == community, CollectTarget.enabled.is_(True))
                .order_by(CollectTarget.id.asc())
            )
        )
        sources = [
            {
                "name": t.display_name or t.target_value,
                "platform": t.platform,
                "target_type": t.target_type,
                "last_success_at": t.last_success_at,
                "consecutive_failures": t.consecutive_failures,
            }
            for t in target_rows
        ]

        updated_at = None
        stale = False
        if community == XIAOHEIHE_KEY:
            st = await xiaoheihe_feed_status(session)
            updated_at = st.updated_at
            stale = st.stale
        else:
            ts_list = [t.last_success_at for t in target_rows if t.last_success_at]
            if ts_list:
                updated_at = max(ts_list)
            elif rows:
                updated_at = rows[0][0].collected_at
            stale = bool(target_rows and all(t.last_error for t in target_rows))

        topics = [
            {"name": name, "count": count}
            for name, count in sorted(topic_counter.items(), key=lambda p: (-p[1], p[0].lower()))[:14]
        ]

        meta = COMMUNITIES.get(community)
        return {
            "community": community,
            "total_count": total_count,
            "recent_24h_count": recent_24h,
            "brief_count": brief_count,
            "ai_summary": bool(meta and meta.ai_summary),
            "updated_at": updated_at,
            "stale": stale,
            "lead": lead,
            "hot_items": hot_items,
            "topics": topics,
            "sources": sources,
        }

    return await cached_dict(cache, cache_key, PUBLIC_CACHE_SECONDS, _load)


@router.get("/git-trending/editions")
async def git_trending_editions(
    request: Request,
    community: CommunitySlug = Query(...),
    period: str | None = Query(default=None, pattern="^(|daily|weekly|monthly)$"),
    period_key: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=30, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
):
    """GitHub Trending 分期刊物（日刊 / 周刊 / 月刊）：返回跨期去重与上榜打标后的完整榜单及往期目录。"""
    from app.services.git_trending import get_trending_editions_view

    clean_period = period or None
    clean_key = (period_key or "").strip() or None
    cache = getattr(request.app.state, "redis", None)
    cache_key = (
        f"{CACHE_PREFIX}git-trending:{community}:{clean_period or 'auto'}:{clean_key or 'latest'}:{limit}"
    )
    return await cached_dict(
        cache,
        cache_key,
        PUBLIC_CACHE_SECONDS,
        lambda: get_trending_editions_view(
            session,
            community=community,
            period=clean_period,
            period_key=clean_key,
            limit=limit,
        ),
    )


@router.get("/steam/pulse")
async def steam_pulse(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    hours: int = Query(default=168, ge=24, le=720),
    session: AsyncSession = Depends(get_session),
):
    """分区或聚合首页（all）的 Steam 实时在线人数与好评率趋势；未接入 Steam 时返回空列表。"""
    from app.services.steam import get_steam_pulse

    blocks = request_blocks(request).scoped(community)
    if "steam" in blocks.raw_platforms("game"):
        return {"community": community, "hours": hours, "apps": []}
    cache = getattr(request.app.state, "redis", None)
    cache_key = (
        f"{CACHE_PREFIX}steam-pulse:{community}:{hours}"
        if (not blocks and hours in _STEAM_CACHE_HOURS)
        else None
    )
    return await cached_dict(
        cache,
        cache_key,
        PUBLIC_CACHE_SECONDS,
        lambda: get_steam_pulse(
            session,
            community,
            hours=hours,
            exclude_communities=blocks.communities,
        ),
    )
