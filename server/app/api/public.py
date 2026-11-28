"""公开 API：内容流、来源健康、统计。只暴露可公开的信息（无错误详情、无内部字段）。

公开页当前展示"原始内容流"；事件聚合视图属于事件引擎阶段（设计文档 §4）。
"""

import asyncio
import logging
import re
import time
from datetime import date, timedelta

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
    PublicContentItem,
    PublicContentList,
    PublicSource,
    PublicSourceList,
    PublicStats,
    StatisticsOverview,
)
from app.services.hidden_communities import request_blocks
from app.services.html import sanitize_html as _sanitize_html
from app.services.public_cache import (
    CACHE_PREFIX,
    SITE_CONFIG_KEY,
    cached_dict,
    cached_model,
    invalidate_official_links_cache,  # noqa: F401 - 测试兼容导出
    official_links_cache as _official_links_cache,  # noqa: F401 - 测试兼容导出
    sitemap_cache as _SITEMAP_CACHE,  # noqa: F401 - 测试兼容导出
)

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
