"""公开 API：内容流、来源健康、统计。只暴露可公开的信息（无错误详情、无内部字段）。

公开页当前展示"原始内容流"；事件聚合视图属于事件引擎阶段（设计文档 §4）。
"""

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import BigInteger, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer

from app.communities import (
    COMMUNITIES,
    COMMUNITY_AGGREGATE_QUERY_DEP,
    COMMUNITY_QUERY_DEP,
    CommunityOrAllSlug,
    CommunitySlug,
    DEFAULT_COMMUNITY,
    community_conditions,
    community_to_public_dict,
    is_all_community,
    sync_communities,
)
from app.db import get_session
from app.clock import utcnow
from app.models import CollectTarget, ContentReview, ContentVersion, Evidence, RawContent, Source
from app.schemas.api import (
    ContentDetailOut,
    ContentVersionHistoryOut,
    EventListOut,
    EventOut,
    ProductListOut,
    ProductOut,
    ProductSignalListOut,
    PublicContentItem,
    PublicContentList,
    PublicSource,
    PublicSourceList,
    PublicStats,
    StatisticsOverview,
)
from app.services.hidden_communities import request_blocks, request_hidden
from app.services.html import sanitize_html as _sanitize_html
from app.services.public_cache import (
    CACHE_PREFIX,
    SITE_CONFIG_KEY,
    cached_dict,
    cached_model,
    invalidate_official_links_cache,
    official_links_cache as _official_links_cache,
)
from app.services.urls import safe_link, safe_media_url

router = APIRouter(prefix="/api")

_EXCERPT_LEN = 140

# 公开列表的分页上界：深分页在 MySQL 里是「先扫再丢」，一个无鉴权请求就能触发
# 数十万行扫描。10000 足够覆盖正常浏览（前端最多到 334 页 × 30），再深请用筛选。
MAX_PUBLIC_OFFSET = 10_000
# 热门排序要逐条取最新指标，时间跨度越大越慢：30 天仍要 2 秒以上。默认值与站内入口的 7 天一致。
HOT_DEFAULT_WINDOW_HOURS = 168

# 公开分区参数：运行时通过 Depends 校验动态注册表（非法值返回 422）
COMMUNITY_QUERY = COMMUNITY_QUERY_DEP
COMMUNITY_AGGREGATE_QUERY = COMMUNITY_AGGREGATE_QUERY_DEP


HIGHLIGHTS_CACHE_SECONDS = 60
PUBLIC_CACHE_SECONDS = 60
EVENTS_CACHE_SECONDS = 45
_TRENDING_CACHE_HOURS = frozenset({6, 12, 24, 48, 72, 168})
_TRENDING_CACHE_LIMITS = frozenset({5, 10, 20})
_EVENTS_CACHE_LIMITS = frozenset({6, 20, 30})
_STEAM_CACHE_HOURS = frozenset({24, 72, 168, 720})


@router.get("/highlights", response_model=PublicContentList)
async def list_highlights(
    request: Request,
    limit: int = Query(default=5, ge=1, le=8),
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """首页精选：官方实质动态与高热度社区内容混排。

    候选要扫描近 200 条内容并补指标与事件分组（实测 0.6–0.7s），是首页首屏最慢的一步；
    仅缓存默认公共视角，避免自定义屏蔽 cookie 组合产生高基数 Key 占用 Redis 内存。
    """
    from app.services.curation import select_highlights

    blocks = request_blocks(request).scoped(community)
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}highlights:{community}:{limit}" if not blocks else None

    async def _load() -> PublicContentList:
        items = [
            PublicContentItem(**item)
            for item in await select_highlights(
                session, limit, community=community, exclude=blocks.communities,
                where=blocks.platform_conditions(RawContent.platform, RawContent.community),
            )
        ]
        return PublicContentList(items=items, total=len(items))

    return await cached_model(cache, cache_key, HIGHLIGHTS_CACHE_SECONDS, PublicContentList, _load)


@router.get("/trending", response_model=PublicContentList)
async def list_trending(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    hours: int = Query(default=24, ge=6, le=168, description="增量统计窗口"),
    limit: int = Query(default=10, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
):
    """热度飙升：窗口内播放增量最高的内容（需 ≥2 个指标快照）。"""
    from app.services.curation import select_trending

    blocks = request_blocks(request).scoped(community)
    cache = getattr(request.app.state, "redis", None)
    cache_key = (
        f"{CACHE_PREFIX}trending:{community}:{hours}:{limit}"
        if (not blocks and hours in _TRENDING_CACHE_HOURS and limit in _TRENDING_CACHE_LIMITS)
        else None
    )

    async def _load() -> PublicContentList:
        items = [
            PublicContentItem(**item)
            for item in await select_trending(
                session, limit, community=community, hours=hours, exclude=blocks.communities,
                where=blocks.platform_conditions(RawContent.platform, RawContent.community),
            )
        ]
        return PublicContentList(items=items, total=len(items))

    return await cached_model(cache, cache_key, PUBLIC_CACHE_SECONDS, PublicContentList, _load)


@router.get("/calendar")
async def content_calendar(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    from app.services.calendar import calendar_groups

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}calendar:{community}" if not hidden else None
    return await cached_dict(
        cache,
        cache_key,
        PUBLIC_CACHE_SECONDS,
        lambda: calendar_groups(session, community, exclude=hidden),
    )


@router.get("/calendar/month")
async def monthly_calendar(
    request: Request,
    month: str = Query(pattern=r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$"),
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    from app.services.calendar import calendar_month

    return await calendar_month(session, community, month, exclude=request_hidden(request))


@router.get("/calendar/range")
async def calendar_range_api(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    start: str | None = Query(default=None, pattern=r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$"),
    days: int = Query(default=30, ge=7, le=90),
    days_back: int = Query(default=3, ge=0, le=30),
    session: AsyncSession = Depends(get_session),
):
    """跨月时间轴窗口日程（供活动日历甘特时间轴视图使用）。"""
    from app.services.calendar import calendar_range

    if start is not None:
        try:
            datetime.strptime(start, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(400, "无效日期") from exc

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = (
        f"{CACHE_PREFIX}calendar_range:{community}:{days}:{days_back}"
        if (not hidden and start is None and days in {14, 21, 30, 45} and days_back == 3)
        else None
    )
    return await cached_dict(
        cache,
        cache_key,
        PUBLIC_CACHE_SECONDS,
        lambda: calendar_range(
            session,
            community,
            start_date=start,
            days=days,
            days_back=days_back,
            exclude=hidden,
        ),
    )


@router.get("/calendar.ics")
async def calendar_subscription_feed(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """整分区/全站活动日历订阅流（支持 webcal:// 与系统日历自动刷新）。"""
    from app.services.calendar import calendar_entries, calendar_feed_ics

    cache = getattr(request.app.state, "redis", None)
    base = public_site_base(request)
    cache_key = f"{CACHE_PREFIX}calendar_ics:{community}"

    async def _load() -> dict:
        now = utcnow()
        entries = await calendar_entries(
            session,
            community,
            now=now,
            window_start=now - timedelta(days=14),
            window_end=now + timedelta(days=120),
        )
        all_mode = is_all_community(community)
        label = "全部分区" if all_mode else (COMMUNITIES.get(community).label if community in COMMUNITIES else community)
        labels = {key: meta.label for key, meta in COMMUNITIES.items()}
        ics = calendar_feed_ics(
            entries,
            cal_name=f"AniSignal 活动日历 · {label}",
            community_labels=labels,
            site_base=base,
            include_community_prefix=all_mode,
        )
        return {"ics": ics}

    payload = await cached_dict(cache, cache_key, PUBLIC_CACHE_SECONDS, _load)
    return Response(
        payload["ics"],
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="anisignal-{community}.ics"',
            "Cache-Control": "public, max-age=300",
        },
    )


@router.get("/calendar/{schedule_id}.ics")
async def export_calendar(schedule_id: int, session: AsyncSession = Depends(get_session)):
    from app.models import ContentSchedule
    from app.services.calendar import calendar_ics

    row = (await session.execute(
        select(ContentSchedule, RawContent.url)
        .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
        .where(ContentSchedule.id == schedule_id, RawContent.hidden.is_(False))
    )).first()
    if row is None:
        raise HTTPException(404, "日程不存在")
    return Response(calendar_ics(*row), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="anisignal-{schedule_id}.ics"'})


@router.get("/codes")
async def list_redeem_codes(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """可用兑换码列表（官方 + 社区 UP 主双轨抽取；过期或超 14 天未知有效期自动隐藏）。"""
    from app.models import RedeemCode
    from app.services.redeem_codes import active_redeem_code_conditions

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}codes:{community}" if not hidden else None

    async def _load() -> dict:
        now = utcnow()
        conditions: list = [
            *active_redeem_code_conditions(now),
            *community_conditions(RedeemCode.community, community, hidden),
        ]
        rows = await session.execute(
            select(RedeemCode, RawContent.url, RawContent.published_at)
            .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
            .where(*conditions)
            .order_by(
                (RedeemCode.source_kind == "official").desc(),
                RedeemCode.source_count.desc(),
                RedeemCode.created_at.desc(),
            )
            .limit(40)
        )
        items = [
            {
                "code": code.code,
                "community": code.community,
                "expires_at": code.expires_at,
                "created_at": code.created_at,
                "published_at": published_at,
                "source_kind": code.source_kind or "official",
                "source_count": code.source_count or 1,
                "server": code.server,
                "reward": code.reward,
                "expiry_estimated": bool(code.expiry_estimated),
                # 兑换码页把 url 直接绑到 href，脏 scheme 不下发
                "url": safe_link(url),
            }
            for code, url, published_at in rows
        ]
        return {"items": items, "total": len(items)}

    return await cached_dict(cache, cache_key, PUBLIC_CACHE_SECONDS, _load)


def _preview_mode(text: str | None, media: list, url: str | None) -> str:
    if text and text.strip():
        return "full"
    if media:
        return "partial"
    return "external_only"


def _merge_topics(extra: dict | None, brief: dict | None, *, limit: int = 5) -> list[str]:
    """合并来源原生话题与 AI 解读标签，保序去重。"""
    raw_topics = (extra or {}).get("topics") or []
    brief_tags = (brief or {}).get("tags") or []
    return list(
        dict.fromkeys(
            t
            for t in (
                *(str(x).strip() for x in raw_topics),
                *(str(x).strip() for x in brief_tags),
            )
            if t
        )
    )[:limit]


@router.get("/contents", response_model=PublicContentList)
async def list_contents(
    request: Request,
    platform: str | None = Query(default=None),
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    content_type: str | None = Query(default=None),
    identity: str | None = Query(default=None, description="official/user/... 来源身份过滤"),
    exclude_identity: str | None = Query(default=None, max_length=32, description="排除指定来源身份"),
    published_within_hours: int | None = Query(
        default=None, ge=1, le=8760, description="仅返回最近若干小时发布的内容"
    ),
    include_chat: bool = Query(default=False, description="包含闲聊/杂谈类（默认隐藏降噪）"),
    revised_only: bool = Query(default=False, description="仅返回有实质修订记录的内容"),
    sort: str = Query(default="time", pattern="^(time|hot)$", description="hot=按最新播放量排序"),
    q: str | None = Query(default=None, max_length=100, description="标题关键词"),
    topic: str | None = Query(default=None, max_length=64, description="主题/AI 标签筛选"),
    limit: int = Query(default=30, ge=1, le=100),
    # 公开端点无鉴权：offset 不封顶时 `?offset=2999940` 就是一次全表扫的免费入口
    offset: int = Query(default=0, ge=0, le=MAX_PUBLIC_OFFSET),
    session: AsyncSession = Depends(get_session),
):
    from app.services.classify import HIDDEN_CATEGORIES
    from app.services.xiaoheihe import XIAOHEIHE_KEY, xiaoheihe_feed_status

    is_xiaoheihe = community == XIAOHEIHE_KEY
    blocks = request_blocks(request).scoped(community)
    conditions: list = community_conditions(RawContent.community, community, blocks.communities)
    if platform:
        conditions.append(
            RawContent.platform.in_(("kuro_cn", "kuro_global"))
            if platform == "kuro"
            else RawContent.platform == platform
        )
    else:
        # 显式选中某个平台时照常显示，与直接打开被屏蔽分区的规则一致。
        conditions.extend(blocks.platform_conditions(RawContent.platform, RawContent.community))
    if content_type:
        conditions.append(RawContent.content_type == content_type)
    if identity:
        conditions.append(Source.identity == identity)
    if exclude_identity:
        conditions.append((Source.identity != exclude_identity) | Source.identity.is_(None))
    published = func.coalesce(RawContent.published_at, RawContent.collected_at) if is_xiaoheihe else RawContent.published_at
    xiaoheihe_hot_fallback = is_xiaoheihe and sort == "hot" and published_within_hours is None
    if sort == "hot" and published_within_hours is None:
        published_within_hours = HOT_DEFAULT_WINDOW_HOURS
    window_condition = None
    if published_within_hours:
        window_condition = published >= utcnow() - timedelta(hours=published_within_hours)
        conditions.append(window_condition)
    conditions.append(RawContent.hidden.is_(False))
    from app.services import settings as settings_service

    hidden_platforms = settings_service.hidden_platforms(
        await settings_service.get_media_visibility(session)
    )
    if hidden_platforms:
        conditions.append(RawContent.platform.notin_(hidden_platforms))
    if not include_chat:
        conditions.append(ContentVersion.category.notin_(HIDDEN_CATEGORIES) | ContentVersion.category.is_(None))
    filter_revised = revised_only is True
    if filter_revised:
        prev_cv = aliased(ContentVersion, name="cv_prev")
        conditions.append(ContentVersion.version > 1)
        conditions.append(
            exists(
                select(1).where(
                    prev_cv.raw_content_id == RawContent.id,
                    prev_cv.version < ContentVersion.version,
                    or_(
                        func.coalesce(prev_cv.title, "") != func.coalesce(ContentVersion.title, ""),
                        func.coalesce(prev_cv.text, "") != func.coalesce(ContentVersion.text, ""),
                        func.coalesce(prev_cv.normalized_json["media"].as_string(), "")
                        != func.coalesce(ContentVersion.normalized_json["media"].as_string(), ""),
                    ),
                )
            )
        )
    if q:
        from app.services.search import content_search_condition, supports_mysql_fts

        search_cond = content_search_condition(q, use_mysql_fts=await supports_mysql_fts(session))
        if search_cond is not None:
            conditions.append(search_cond)

    # 直接通过 raw_contents.latest_version_id 主键关联最新版本，避免每页执行关联 NOT EXISTS 或 GROUP BY 衍生表；
    # 仍分两步取：先按覆盖索引定位本页 id，再回表读正文和 JSON，防止宽行排序退化。
    def matching(*columns, active_conditions=None):
        query = (
            select(*columns)
            .select_from(RawContent)
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .where(*(conditions if active_conditions is None else active_conditions))
        )
        # 只有身份筛选要用来源表
        return query.join(Source, RawContent.source_id == Source.id, isouter=True) if identity or exclude_identity else query

    latest_score = None
    if sort == "hot":
        from app.models import MetricSnapshot

        # 非小黑盒分区直接读取原生整数列 view_count，命中 (raw_content_id, id, view_count) 覆盖索引，免去 JSON_EXTRACT。
        if is_xiaoheihe:
            score = func.coalesce(
                MetricSnapshot.view_count,
                func.coalesce(cast(MetricSnapshot.metrics["like"].as_string(), BigInteger), 0)
                + func.coalesce(cast(MetricSnapshot.metrics["reply"].as_string(), BigInteger), 0),
            )
        else:
            score = MetricSnapshot.view_count
        latest_score = (
            select(score)
            .where(MetricSnapshot.raw_content_id == RawContent.id)
            .order_by(MetricSnapshot.id.desc())
            .limit(1)
            .correlate(RawContent)
            .scalar_subquery()
        )
    from app.services.content_brief import load_briefs_map
    from app.services.content_diff import RevisionSummary, load_revision_summaries

    clean_topic = topic.strip() if isinstance(topic, str) and topic.strip() else None

    async def _build_list_response() -> PublicContentList:
        active_conditions = list(conditions)
        if xiaoheihe_hot_fallback and window_condition is not None:
            recent_total = await session.scalar(matching(func.count(), active_conditions=active_conditions)) or 0
            if recent_total == 0:
                active_conditions = [cond for cond in active_conditions if cond is not window_condition]
        page_query = matching(RawContent.id, active_conditions=active_conditions)
        if latest_score is not None:
            page_query = page_query.order_by(func.coalesce(latest_score, 0).desc())
        ordered_query = page_query.order_by(published.desc(), RawContent.id.desc())
        rev_summaries: dict[int, RevisionSummary] = {}
        if filter_revised:
            cand_query = matching(RawContent.id, RawContent.platform, active_conditions=active_conditions)
            if latest_score is not None:
                cand_query = cand_query.order_by(func.coalesce(latest_score, 0).desc())
            cand_rows = list(
                (await session.execute(cand_query.order_by(published.desc(), RawContent.id.desc()).limit(250))).all()
            )
            candidate_ids = [cid for cid, _plat in cand_rows]
            candidate_platforms = {cid: plat for cid, plat in cand_rows}
            rev_summaries = await load_revision_summaries(
                session, candidate_ids, platforms=candidate_platforms
            )
            verified_ids = [
                cid for cid in candidate_ids
                if (summary := rev_summaries.get(cid)) is not None and summary.revision_count > 0
            ]
            total = len(verified_ids)
            page_ids = verified_ids[offset : offset + limit]
        elif clean_topic:
            # 主题/AI 标签存在 JSON 中，在 SQLite（ASCII 转义）与 MySQL 间按候选精确匹配保证一致性；
            # 两阶段先取排序后的 ID 再水合 normalized_json，避免大 JSON 进 MySQL sort_buffer
            needle = clean_topic.casefold()
            cand_ids = list(await session.scalars(ordered_query.limit(500)))
            norm_map = dict(
                (
                    await session.execute(
                        select(RawContent.id, ContentVersion.normalized_json)
                        .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
                        .where(RawContent.id.in_(cand_ids))
                    )
                ).all()
            ) if cand_ids else {}
            cand_rows = [(cid, norm_map.get(cid)) for cid in cand_ids]
            cand_briefs = await load_briefs_map(session, cand_ids)
            matched_ids: list[int] = []
            for cid, norm in cand_rows:
                extra = (norm or {}).get("extra") or {}
                item_topics = [str(t).strip() for t in (extra.get("topics") or []) if str(t).strip()]
                brief_tags = [str(t).strip() for t in ((cand_briefs.get(cid) or {}).get("tags") or []) if str(t).strip()]
                if any(t.casefold() == needle for t in (*item_topics, *brief_tags)):
                    matched_ids.append(cid)
            total = len(matched_ids)
            page_ids = matched_ids[offset : offset + limit]
        else:
            page_ids = list(await session.scalars(ordered_query.limit(limit).offset(offset)))
            total = await session.scalar(matching(func.count(), active_conditions=active_conditions)) or 0

        rows = await session.execute(
            select(RawContent, Source.name, Source.identity, ContentVersion)
            .options(defer(RawContent.raw_json))
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(RawContent.id.in_(page_ids))
        ) if page_ids else []
        page_rows = {row[0].id: row for row in rows}
        briefs_map = await load_briefs_map(session, page_ids) if page_ids else {}
        if not filter_revised:
            multi_platforms = {
                raw.id: raw.platform
                for raw, _, _, ver in page_rows.values()
                if ver is not None and ver.version > 1
            }
            if multi_platforms:
                rev_summaries = await load_revision_summaries(
                    session, list(multi_platforms), platforms=multi_platforms
                )

        candidates: list[PublicContentItem] = []
        for raw, source_name, source_identity, version in (page_rows[raw_id] for raw_id in page_ids):
            normalized = version.normalized_json if version else None
            extra = (normalized or {}).get("extra") or {}
            text = (version.text if version else None) or str((normalized or {}).get("text") or "")
            raw_media = (normalized or {}).get("media") or []
            media = [m for m in raw_media if isinstance(m, str)] if isinstance(raw_media, list) else []
            brief_data = briefs_map.get(raw.id)
            merged_topics = _merge_topics(extra, brief_data, limit=5)
            rev = rev_summaries.get(raw.id)
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
                    cover=media[0] if media else None,
                    media_list=media[:3],
                    media_count=len(media),
                    topics=merged_topics,
                    brief=brief_data,
                    preview_mode="partial" if extra.get("truncated") else _preview_mode(text, media, raw.url),
                    matched_keywords=(raw.matched_keywords or []),
                    community=(raw.community or DEFAULT_COMMUNITY),
                    revision_count=rev.revision_count if rev else 0,
                    has_source_revision=rev.has_source_revision if rev else False,
                )
            )

        metrics_map = await _latest_metrics(session, [i.id for i in candidates])
        for item in candidates:
            item.metrics = metrics_map.get(item.id)
        items = candidates

        if is_xiaoheihe:
            return PublicContentList(items=items, total=total, feed_status=await xiaoheihe_feed_status(session))
        return PublicContentList(items=items, total=total)

    if (
        filter_revised
        and request is not None
        and not blocks
        and not q
        and not hidden_platforms
        and offset == 0
        and published_within_hours is None
        and not include_chat
        and not exclude_identity
        and not content_type
        and limit in (20, 30)
    ):
        cache = getattr(request.app.state, "redis", None)
        cache_key = (
            f"{CACHE_PREFIX}contents-revised:{community}:{platform or 'all'}:"
            f"{identity or 'all'}:{sort}:{limit}"
        )
        return await cached_model(cache, cache_key, PUBLIC_CACHE_SECONDS, PublicContentList, _build_list_response)
    return await _build_list_response()


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


_RSS_CACHE: dict[str, tuple[float, str]] = {}
_RSS_TTL_SECONDS = 300


_SITEMAP_CACHE: dict[str, tuple[float, str]] = {}
_SITEMAP_TTL_SECONDS = 3600
_SITEMAP_PAGES = (
    "/", "/ai", "/ai/reports", "/ai/sources", "/official", "/calendar", "/events", "/products", "/steam", "/contents",
    "/sources", "/stats", "/about", "/intro", "/project",
)


def public_site_base(request: Request | None = None) -> str:
    """对外站点基址：优先 ANISIGNAL_PUBLIC_BASE_URL，否则根据反代头或请求基址还原。"""
    from app.config import get_settings

    configured = get_settings().public_base_url.strip().rstrip("/")
    if configured:
        return configured
    if request is None:
        return "http://127.0.0.1:8090"
    host = (
        request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        or request.headers.get("host", "").strip()
    )
    proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        or request.url.scheme
        or "http"
    )
    if (
        proto in {"http", "https"}
        and host
        and host not in {"127.0.0.1:8000", "localhost:8000", "api:8000"}
        and re.fullmatch(r"[A-Za-z0-9._:\[\]-]{1,255}", host)
    ):
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


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
    from sqlalchemy import or_

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


@router.get("/sources", response_model=PublicSourceList)
async def list_sources(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """公开的来源健康：已接入来源、最近成功采集时间与不可用状态（设计文档 §6/§16）。

    community=all 时返回全部分区的来源（系统级视角）；不暴露 last_error 等内部细节。
    """
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}sources:{community}"

    async def _load() -> PublicSourceList:
        rows = await session.execute(
            select(CollectTarget)
            .where(*([] if is_all_community(community) else [CollectTarget.community == community]))
            .order_by(CollectTarget.community, CollectTarget.platform, CollectTarget.id)
        )
        items = [
            PublicSource(
                name=target.display_name or target.target_value,
                platform=target.platform,
                target_type=target.target_type,
                enabled=target.enabled,
                last_success_at=target.last_success_at,
                consecutive_failures=target.consecutive_failures,
                community=getattr(target, "community", None) or DEFAULT_COMMUNITY,
            )
            for target in rows.scalars()
        ]
        return PublicSourceList(items=items)

    return await cached_model(cache, cache_key, PUBLIC_CACHE_SECONDS, PublicSourceList, _load)


@router.get("/contents/{content_id}/notification-image.jpg")
async def content_notification_image(content_id: int, session: AsyncSession = Depends(get_session)):
    from fastapi.responses import RedirectResponse

    from app.services.bot_media import bilibili_images, render_notification_image

    normalized = await session.scalar(select(ContentVersion.normalized_json).join(
        RawContent, RawContent.id == ContentVersion.raw_content_id).where(
        RawContent.id == content_id, RawContent.hidden.is_(False),
        RawContent.platform == "bilibili", RawContent.content_type == "dynamic"
    ).order_by(ContentVersion.version.desc()).limit(1))
    images = bilibili_images((normalized or {}).get("media") or [])
    if not images:
        raise HTTPException(status_code=404, detail="图片不存在")
    if len(images) < 3:
        return RedirectResponse(images[0])
    try:
        content = await render_notification_image(images)
    except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
        logging.getLogger(__name__).warning("Bot image unavailable content=%s (%s)", content_id, type(exc).__name__)
        return RedirectResponse(images[0], headers={"Cache-Control": "no-store"})
    if content is None:
        return RedirectResponse(images[0], headers={"Cache-Control": "public, max-age=3600"})
    return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


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
.where(RawContent.id == content_id, RawContent.hidden.is_(False))
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
        .where(Evidence.raw_content_id == content_id, EventModel.is_active.is_(True), EventModel.category != "ai_news")
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

    rev_summary = None
    if version is not None and version.version > 1:
        from app.services.content_diff import load_revision_summaries

        rev_summary = (
            await load_revision_summaries(session, [raw.id], platforms={raw.id: raw.platform})
        ).get(raw.id)

    review = await session.get(ContentReview, raw.id) if raw.community == "ai" else None
    news = review.news if (
        review and review.news and version
        and review.news.get("content_hash") == raw.content_hash
        and review.news.get("content_version") == version.version
    ) else None

    from app.models import ContentBrief
    from app.services.content_brief import serialize_brief

    brief_row = await session.get(ContentBrief, content_id)
    brief = serialize_brief(brief_row)
    extra = normalized.get("extra") or {}
    topics = _merge_topics(extra, brief, limit=8)

    repo_meta = None
    if raw.platform == "github":
        repo_name = str(extra.get("repo") or "").strip() or None
        trending_history = []
        if raw.content_type == "repo" and repo_name:
            from app.services.git_trending import get_repo_trending_history

            trending_history = await get_repo_trending_history(
                session, community=raw.community, repo=repo_name
            )
        repo_meta = {
            "repo": repo_name,
            "kind": extra.get("kind"),
            "language": extra.get("language"),
            "stars": extra.get("stars"),
            "forks": extra.get("forks"),
            "period_stars": extra.get("period_stars"),
            "tag_name": extra.get("tag_name"),
            "prerelease": bool(extra.get("prerelease")),
            "readme": extra.get("readme") or None,
            "trending_history": trending_history,
        }

    return ContentDetailOut(
        id=raw.id,
        community=raw.community,
        news={k: v for k, v in news.items() if k not in {"content_hash", "content_version"}} if news else None,
        platform=raw.platform,
        content_type=raw.content_type,
        external_id=raw.external_id,
        url=raw.url,
        title=(version.title if version else None) or raw.external_id,
        text=text or None,
        text_html=_sanitize_html(text) if text else None,
        text_truncated=bool(extra.get("truncated")),
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
        brief=brief,
        topics=topics,
        repo_meta=repo_meta,
        matched_keywords=(raw.matched_keywords or []),
        version=version.version if version else None,
        revision_count=rev_summary.revision_count if rev_summary else 0,
        has_source_revision=rev_summary.has_source_revision if rev_summary else False,
        preview_mode="partial" if extra.get("truncated") else _preview_mode(text, media, raw.url),
    )


@router.get("/contents/{content_id}/versions", response_model=ContentVersionHistoryOut)
async def get_content_versions(
    request: Request,
    content_id: int,
    from_version: int | None = Query(default=None, ge=1, description="对比基准版本号"),
    to_version: int | None = Query(default=None, ge=1, description="对比目标版本号"),
    session: AsyncSession = Depends(get_session),
):
    """返回单条内容的实质版本列表与文本/标题/图片 Diff（自动折叠解析重算与非实质抖动）。"""
    from app.services.content_diff import build_version_history, load_version_inputs

    meta_row = (
        await session.execute(
            select(RawContent.platform, RawContent.latest_version_id)
            .where(RawContent.id == content_id, RawContent.hidden.is_(False))
        )
    ).first()
    if meta_row is None:
        raise HTTPException(status_code=404, detail="content not found")
    platform, latest_version_id = meta_row

    async def _load() -> ContentVersionHistoryOut:
        versions = (await load_version_inputs(session, [content_id])).get(content_id, [])
        try:
            payload = build_version_history(
                content_id,
                versions,
                platform=platform,
                from_version=from_version,
                to_version=to_version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return ContentVersionHistoryOut(**payload)

    cache = getattr(request.app.state, "redis", None)
    # 缓存键纳入 latest_version_id，内容新增版本或修订时自动换键，无陈旧缓存风险。
    cache_key = (
        f"{CACHE_PREFIX}content-versions:{content_id}:{latest_version_id or 0}:"
        f"{from_version or 0}:{to_version or 0}"
    )
    return await cached_model(cache, cache_key, PUBLIC_CACHE_SECONDS, ContentVersionHistoryOut, _load)

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
        from fastapi import HTTPException

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
    from fastapi import HTTPException

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


@router.get("/site-config")
async def site_config(request: Request, session: AsyncSession = Depends(get_session)):
    """公开站点配置：全服公告、文案区域、动态分区与服务就绪布尔状态（不含任何凭据或 SMTP 详情）。"""
    from app.services import settings as settings_service
    from app.services.ai import ai_available, get_ai_config

    cache = getattr(request.app.state, "redis", None)

    async def _load() -> dict:
        await sync_communities(session)
        all_items = [community_to_public_dict(meta) for meta in COMMUNITIES.values()]
        ai_cfg = await get_ai_config(session)
        smtp_cfg = await settings_service.get_effective_smtp(session)
        return {
            "announcement": await settings_service.get_announcement(session),
            "texts": await settings_service.get_texts(session),
            "communities": [item for item in all_items if item["active"]],
            "all_communities": all_items,
            "media_visibility": await settings_service.get_media_visibility(session),
            "ai_configured": bool(ai_available(ai_cfg)),
            "smtp_configured": bool(smtp_cfg.get("host") and smtp_cfg.get("user") and smtp_cfg.get("password")),
        }

    return await cached_dict(cache, SITE_CONFIG_KEY, PUBLIC_CACHE_SECONDS, _load)


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
# 显示走图床 CDN 直连（用户实测 4MB/s+，远快于本站出口），服务器只做地址解析与
# 下载代理：解析返回的地址立即签发，下载端点只转发签发过的地址（防开放代理），
# 保证下载的就是当前展示那张。不要把图片字节引到本站出口——用户到本站只有 ~180KB/s。

WALLPAPER_API = "https://api.yppp.net/api.php"
# 同一解析结果最短复用时间：防手速刷新打爆源站，同时保证"每次刷新基本换图"
WALLPAPER_MIN_INTERVAL_SECONDS = 2.0
# 签发记录保留时长
WALLPAPER_ISSUED_TTL_SECONDS = 900.0

_wallpaper_last_resolve = 0.0
_wallpaper_last_url: str | None = None
_wallpaper_issued: dict[str, float] = {}
_wallpaper_lock = asyncio.Lock()

logger = logging.getLogger(__name__)


def _prune_issued(now: float) -> None:
    expired = [u for u, ts in _wallpaper_issued.items() if now - ts > WALLPAPER_ISSUED_TTL_SECONDS]
    for u in expired:
        _wallpaper_issued.pop(u, None)


def _ensure_issued(url: str) -> None:
    now = time.monotonic()
    _prune_issued(now)
    issued_at = _wallpaper_issued.get(url)
    if issued_at is None or now - issued_at > WALLPAPER_ISSUED_TTL_SECONDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "壁纸地址已过期，请刷新页面后重试")


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


@router.get("/feeds/overview")
async def feed_overview(
    request: Request,
    community: CommunitySlug = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """独立主题流信号台总览（小黑盒 / 开源 / 自建 feed 分区）：头图焦点、近期热门、话题分布与来源健康。"""
    from collections import Counter

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


@router.get("/wallpaper")
async def wallpaper_info():
    """随机壁纸：返回图床原图直连地址（每次调用基本不同，由浏览器直连 CDN 显示）。"""
    url = await _resolve_wallpaper()
    if url is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "壁纸源暂时不可用")
    return {"url": url}


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
