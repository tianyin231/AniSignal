"""首页精选 + 入库审核：AI 逐条多模态评估（标题/正文/封面），违规与不相关内容自动下架。"""

import asyncio
import base64
import json
from html import unescape
import logging
import math
import re
import time
import httpx
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import DEFAULT_COMMUNITY, get_community, is_all_community
from app.models import (
    ClassificationRule,
    CollectTarget,
    ContentReview,
    ContentVersion,
    Evidence,
    MetricSnapshot,
    Product,
    RawContent,
    Source,
)
from app.models.enums import Category
from app.services.ai import AiPoolCoolingDown, ai_available, chat, get_ai_config, record_usage
from app.services.classify import HIDDEN_CATEGORIES
from app.services.ai_news import constrain_news, parse_news

REVIEW_CONCURRENCY = 2
# 多个采集目标会同时投递审核；并发上限按 worker 进程共享，避免每批各开一组。
_review_loop = None
_review_semaphore = None


def _shared_review_semaphore():
    global _review_loop, _review_semaphore
    loop = asyncio.get_running_loop()
    if _review_loop is not loop:
        _review_loop, _review_semaphore = loop, asyncio.Semaphore(REVIEW_CONCURRENCY)
    return _review_semaphore

REVIEW_TIMEOUT_SECONDS = 180
HIGHLIGHT_WINDOW = timedelta(days=7)
COMMUNITY_MIN_VIEWS = 10_000
_PROMOTION_TERMS = (
    "抽奖",
    "转发有奖",
    "关注并转发",
    "中奖名单",
    "签到",
    "创作激励",
    "评论区互动",
)
_ALLOWED_CATEGORIES = {item.value for item in Category} | {"chat"}
logger = logging.getLogger(__name__)


@dataclass
class ReviewStats:
    candidates: int = 0
    reviewed: int = 0
    failed_batches: int = 0
    skipped_reason: str | None = None


@dataclass
class HighlightCandidate:
    item: dict
    score: float
    group: str
    official: bool


async def review_contents(
    session: AsyncSession, content_ids: list[int], *, force: bool = False
) -> ReviewStats:
    """逐条多模态审核（标题/正文/封面）；违规或不相关的内容自动下架，管理端可恢复。

    AI 判定并发执行（限流），结果顺序落库；单条失败不影响其余内容。
    """
    stats = ReviewStats()
    ids = list(dict.fromkeys(int(item) for item in content_ids if item))
    if not ids:
        return stats
    config = await get_ai_config(session)
    if not ai_available(config):
        stats.skipped_reason = "ai_not_configured"
        return stats

    tracked_products = await _load_tracked_products(session)
    candidates = await _review_candidates(session, ids, tracked_products, force=force)
    stats.candidates = len(candidates)
    semaphore = _shared_review_semaphore()

    async def run_one(candidate: dict):
        async with semaphore:
            return candidate, await _review_one(config, candidate)

    hidden_ids: list[int] = []
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(run_one(candidate)) for candidate in candidates]
        for outcome in asyncio.as_completed(tasks):
            candidate, (decision, usage, failed, duration_ms) = await outcome
            content_id = candidate["id"]
            is_news = get_community(candidate.get("community")).domain == "news"
            if failed or decision is None or (is_news and decision.get("news") is None):
                stats.failed_batches += 1
                await record_usage(
                    session,
                    task="review_content",
                    config=config,
                    usage=usage,
                    duration_ms=duration_ms,
                    ref_id=content_id,
                    status="failed",
                )
                if is_news:
                    await session.commit()
                continue
            await record_usage(
                session,
                task="review_content",
                config=config,
                usage=usage,
                duration_ms=duration_ms,
                ref_id=content_id,
            )
            raw = await session.get(RawContent, content_id, populate_existing=True, with_for_update=True)
            if raw and raw.content_hash != candidate["content_hash"]:
                # 新版入队可能被当前任务去重，必须让当前任务延迟重试。
                stats.failed_batches += 1
                continue
            current_version = await session.scalar(select(ContentVersion.version).where(
                ContentVersion.raw_content_id == content_id,
            ).order_by(ContentVersion.version.desc()).limit(1).with_for_update())
            if current_version != candidate["content_version"]:
                stats.failed_batches += 1
                continue
            stats.reviewed += 1
            row = await session.get(ContentReview, content_id)
            if row is None:
                row = ContentReview(raw_content_id=content_id, model=config["model"])
                session.add(row)
            row.relevant = decision["relevant"]
            row.promotional = decision["promotional"]
            row.violation = decision["violation"]
            row.quality_score = decision["quality_score"]
            row.category_hint = decision["category_hint"]
            row.reason = decision["reason"]
            row.links = decision["links"]
            row.fulfillment = decision.get("fulfillment")
            if is_news:
                news = constrain_news(decision.get("news"), candidate)
                row.news = {**news, "content_hash": candidate["content_hash"], "content_version": candidate["content_version"]} if news else None
            row.model = config["model"]
            row.updated_at = utcnow()
            if raw is not None:
                # 官方账号来自预先审核的采集目标，不因相关性误判下架（新角色/新活动
                # 常超出模型知识库，见 _REVIEW_SYSTEM_PROMPT 的词表规则）
                should_hide = bool(
                    decision["violation"]
                    or (not decision["relevant"] and candidate.get("identity") != "official")
                )
                if is_news:
                    should_hide = bool(
                        should_hide or not decision["relevant"] or decision["promotional"]
                        or not row.news or not row.news["newsworthy"]
                    )
                if should_hide and not raw.hidden:
                    hidden_ids.append(content_id)
                raw.hidden = should_hide
            if is_news:
                # 快讯逐条落库，一条慢请求不应拖住已完成的整批消息。
                await session.commit()
    await session.commit()
    # 审核带出了 AI 提取的外链中文名，外链聚合面板需要重建
    from app.api.public import invalidate_official_links_cache

    invalidate_official_links_cache()
    if hidden_ids:
        logger.info("content review auto-hidden %s items: %s", len(hidden_ids), hidden_ids)
    return stats


async def _cover_data_url(url: str) -> str | None:
    """服务端抓取封面转 base64 数据图。

    AI 网关无法抓取远程图片 URL（一律 500），必须在本地转成数据图内嵌。
    """
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0), follow_redirects=True
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AniSignal/1.0)"})
        if resp.status_code != 200 or len(resp.content) > 4 * 1024 * 1024:
            return None
        mime = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not mime.startswith("image/"):
            return None
        return f"data:{mime};base64," + base64.b64encode(resp.content).decode()
    except Exception:  # noqa: BLE001 - 封面抓不到就退回纯文本审核
        return None


async def _review_one(
    config: dict, candidate: dict
) -> tuple[dict | None, dict | None, bool, int]:
    """单条审核：文本 JSON + 封面数据图（多模态）。返回 (判定, usage, 是否失败, 耗时)。

    带图调用失败时降级为纯文本重试一次，保证审核不因图片中断；
    限流与超时留给后台补偿，不占着 worker 长时间睡眠重试。
    """
    started = time.monotonic()
    usage = None

    def text_payload() -> str:
        return json.dumps(
            {key: value for key, value in candidate.items() if key != "cover"},
            ensure_ascii=False,
        )

    async def attempt(with_image: bool) -> tuple[str, dict | None]:
        system_prompt = _review_system_prompt(candidate.get("community") or DEFAULT_COMMUNITY)
        cover_url = candidate.get("cover")
        if with_image and cover_url:
            data_url = await _cover_data_url(cover_url)
            if data_url:
                content: "str | list" = [
                    {"type": "text", "text": text_payload()},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            else:
                content = text_payload()  # 封面抓取失败 → 纯文本
        else:
            content = text_payload()
        return await chat(config, system_prompt, content, json_mode=True)

    async def attempt_with_text_fallback() -> tuple[dict, dict | None]:
        try:
            raw_response, usage = await attempt(with_image=True)
            decision = _parse_review(
                raw_response, candidate["id"], bool(candidate.get("product_context"))
            )
            if decision is None:
                raise ValueError("model returned no valid review")
            return decision, usage
        except Exception as cover_exc:  # noqa: BLE001 - 带图失败降级纯文本重试
            if isinstance(cover_exc, (AiPoolCoolingDown, TimeoutError, httpx.TimeoutException)) or (
                isinstance(cover_exc, httpx.HTTPStatusError) and cover_exc.response.status_code in {401, 402, 403, 429}
            ):
                raise
            logger.info(
                "content review with cover failed for %s (%r), retrying text-only",
                candidate.get("id"), cover_exc,
            )
        raw_response, usage = await chat(
            config,
            _review_system_prompt(candidate.get("community") or DEFAULT_COMMUNITY),
            json.dumps({k: v for k, v in candidate.items() if k != "cover"}, ensure_ascii=False),
            json_mode=True,
        )
        decision = _parse_review(
            raw_response, candidate["id"], bool(candidate.get("product_context"))
        )
        if decision is None:
            raise ValueError("model returned no valid review")
        return decision, usage

    try:
        async with asyncio.timeout(REVIEW_TIMEOUT_SECONDS):
            decision, usage = await attempt_with_text_fallback()
            return decision, usage, False, int((time.monotonic() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - 单条失败不阻断其余内容
        logger.warning("content review failed for %s: %r", candidate.get("id"), exc)
        return None, usage, True, int((time.monotonic() - started) * 1000)


async def review_recent_contents(
    session: AsyncSession, *, limit: int = 40, only_missing: bool = True
) -> ReviewStats:
    """重算近期内容，供部署回填和管理端手动触发。"""
    latest = _latest_version_subquery()
    query = (
        select(RawContent.id, ContentReview.raw_content_id)
        .join(latest, latest.c.rid == RawContent.id)
        .join(ContentReview, ContentReview.raw_content_id == RawContent.id, isouter=True)
        .where(
            func.coalesce(RawContent.published_at, RawContent.collected_at)
            >= utcnow() - HIGHLIGHT_WINDOW
        )
        .order_by(func.coalesce(RawContent.published_at, RawContent.collected_at).desc())
        .limit(200)
    )
    rows = await session.execute(query)
    ids = []
    for content_id, reviewed_id in rows:
        if only_missing and reviewed_id is not None:
            continue
        ids.append(content_id)
        if len(ids) >= limit:
            break
    return await review_contents(session, ids, force=not only_missing)


async def select_trending(
    session: AsyncSession, limit: int = 10, community: str = DEFAULT_COMMUNITY, hours: int = 24
) -> list[dict]:
    """热度飙升：窗口内播放增量最高的内容（≥2 个快照才可参与）。

    返回 PublicContentItem 兼容 dict，额外带 delta_view（窗口内播放增量）。
    """
    from app.services import settings as settings_service
    from sqlalchemy import BigInteger, cast
    from sqlalchemy.orm import aliased, defer

    window_start = utcnow() - timedelta(hours=hours)
    latest = _latest_version_subquery()
    conditions: list = [] if is_all_community(community) else [RawContent.community == community]
    conditions.append(RawContent.hidden.is_(False))
    hidden_platforms = settings_service.hidden_platforms(
        await settings_service.get_media_visibility(session)
    )
    if hidden_platforms:
        conditions.append(RawContent.platform.notin_(hidden_platforms))
    conditions.append(
        ContentVersion.category.notin_(HIDDEN_CATEGORIES) | ContentVersion.category.is_(None)
    )

    snap_sq = (
        select(
            MetricSnapshot.raw_content_id.label("rid"),
            func.min(MetricSnapshot.id).label("first_id"),
            func.max(MetricSnapshot.id).label("last_id"),
        )
        .where(MetricSnapshot.captured_at >= window_start)
        .group_by(MetricSnapshot.raw_content_id)
        .having(func.count(MetricSnapshot.id) >= 2)
        .subquery()
    )
    first_snapshot = aliased(MetricSnapshot)
    last_snapshot = aliased(MetricSnapshot)
    # 显式有符号整数转换同时支持 MySQL / SQLite、数字字符串及缺失的播放量。
    first_view = func.coalesce(cast(first_snapshot.metrics["view"].as_string(), BigInteger), 0)
    last_view = func.coalesce(cast(last_snapshot.metrics["view"].as_string(), BigInteger), 0)
    delta = last_view - first_view
    rows = list(
        await session.execute(
            select(
                delta.label("delta_view"),
                last_snapshot.metrics,
                RawContent,
                Source.name,
                Source.identity,
                ContentVersion,
            )
            .options(defer(RawContent.raw_json))
            .select_from(snap_sq)
            .join(first_snapshot, first_snapshot.id == snap_sq.c.first_id)
            .join(last_snapshot, last_snapshot.id == snap_sq.c.last_id)
            .join(RawContent, RawContent.id == snap_sq.c.rid)
            .join(latest, latest.c.rid == RawContent.id)
            .join(
                ContentVersion,
                (ContentVersion.raw_content_id == latest.c.rid)
                & (ContentVersion.version == latest.c.version),
            )
            .join(Source, RawContent.source_id == Source.id, isouter=True)
            .where(*conditions, delta > 0)
            .order_by(delta.desc(), RawContent.id.desc())
            .limit(limit)
        )
    )

    items: list[dict] = []
    for delta_view, metrics, raw, source_name, source_identity, version in rows:
        normalized = version.normalized_json if version else None
        media = (normalized or {}).get("media") or []
        text = (version.text if version else None) or str((normalized or {}).get("text") or "")
        excerpt = text.strip()
        if "<" in excerpt and ">" in excerpt:
            excerpt = re.sub(r"<[^>]+>", "", unescape(excerpt))
        items.append(
            {
                "id": raw.id,
                "platform": raw.platform,
                "content_type": raw.content_type,
                "external_id": raw.external_id,
                "url": raw.url,
                "title": (version.title if version else None) or raw.external_id,
                "text_excerpt": excerpt[:140] + ("…" if len(excerpt) > 140 else ""),
                "published_at": raw.published_at,
                "collected_at": raw.collected_at,
                "source_name": source_name,
                "identity": source_identity or "unknown",
                "category": version.category,
                "metrics": metrics,
                "cover": media[0] if isinstance(media, list) and media else None,
                "preview_mode": (
                    "full" if text and text.strip() else ("partial" if media else "external_only")
                ),
                "matched_keywords": (raw.matched_keywords or []),
                "community": (raw.community or DEFAULT_COMMUNITY),
                "delta_view": delta_view,
            }
        )
    return items


async def select_highlights(
    session: AsyncSession, limit: int = 5, community: str = DEFAULT_COMMUNITY
) -> list[dict]:
    """返回官方与社区混排精选；AI 结果缺失时使用可解释的规则分数。"""
    latest = _latest_version_subquery()
    publication_time = func.coalesce(RawContent.published_at, RawContent.collected_at)
    rows = list(
        await session.execute(
            select(RawContent, Source, ContentVersion, ContentReview)
            .join(latest, latest.c.rid == RawContent.id)
            .join(
                ContentVersion,
                (ContentVersion.raw_content_id == latest.c.rid)
                & (ContentVersion.version == latest.c.version),
            )
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .join(ContentReview, ContentReview.raw_content_id == RawContent.id, isouter=True)
        .where(
            publication_time >= utcnow() - HIGHLIGHT_WINDOW,
            RawContent.hidden.is_(False),
            *([] if is_all_community(community) else [RawContent.community == community]),
        )
        .order_by(publication_time.desc(), RawContent.id.desc())
        .limit(200)
        )
    )
    if not rows:
        return []

    content_ids = [raw.id for raw, _, _, _ in rows]
    metrics = await _latest_metrics(session, content_ids)
    event_groups = await _event_groups(session, content_ids)
    official: list[HighlightCandidate] = []
    community: list[HighlightCandidate] = []
    now = utcnow()

    for raw, source, version, review in rows:
        normalized = version.normalized_json or {}
        media = normalized.get("media") or []
        cover = next((item for item in media if isinstance(item, str) and item), None)
        if not cover:
            continue
        identity = source.identity if source else "unknown"
        is_official = identity == "official"
        category = version.category or (review.category_hint if review else None)
        text = f"{version.title or ''} {version.text or ''}"
        if category in HIDDEN_CATEGORIES or any(term in text for term in _PROMOTION_TERMS):
            continue
        if review and (not review.relevant or review.promotional):
            continue

        item_metrics = metrics.get(raw.id)
        views = _views(item_metrics)
        if not is_official:
            if raw.platform not in ("bilibili", "youtube") or views < COMMUNITY_MIN_VIEWS:
                continue

        published = raw.published_at or raw.collected_at
        age_hours = max(0.0, (now - published).total_seconds() / 3600)
        freshness = max(0.0, 1 - age_hours / (HIGHLIGHT_WINDOW.total_seconds() / 3600))
        quality = review.quality_score if review else (60 if is_official else 50)
        score = (
            quality * 0.75 + freshness * 25
            if is_official
            else quality * 0.45 + math.log10(views + 1) * 8 + freshness * 15
        )
        title = (version.title or raw.external_id).strip()
        duplicate_id = review.duplicate_of_raw_id if review else None
        group = (
            f"event:{event_groups[raw.id]}"
            if raw.id in event_groups
            else f"duplicate:{min(raw.id, duplicate_id)}"
            if duplicate_id
            else f"title:{_title_key(title)}"
        )
        candidate = HighlightCandidate(
            item={
                "id": raw.id,
                "platform": raw.platform,
                "content_type": raw.content_type,
                "external_id": raw.external_id,
                "url": raw.url,
                "title": title,
                "text_excerpt": _excerpt(version.text or str(normalized.get("text") or "")),
                "published_at": raw.published_at,
                "collected_at": raw.collected_at,
                "source_name": source.name if source else None,
                "identity": identity,
                "category": category,
                "metrics": item_metrics,
                "cover": cover,
                "preview_mode": "full" if (version.text or "").strip() else "partial",
                "matched_keywords": raw.matched_keywords or [],
                "community": getattr(raw, "community", None) or DEFAULT_COMMUNITY,
            },
            score=score,
            group=group,
            official=is_official,
        )
        (official if is_official else community).append(candidate)

    official.sort(key=lambda item: item.score, reverse=True)
    community.sort(key=lambda item: item.score, reverse=True)
    selected: list[HighlightCandidate] = []
    used_groups: set[str] = set()

    def pick(pool: list[HighlightCandidate], count: int) -> None:
        for candidate in pool:
            if len([item for item in selected if item.official == candidate.official]) >= count:
                break
            if candidate.group in used_groups:
                continue
            selected.append(candidate)
            used_groups.add(candidate.group)

    pick(official, min(3, limit))
    pick(community, min(2, limit))
    for candidate in sorted([*official, *community], key=lambda item: item.score, reverse=True):
        if len(selected) >= limit:
            break
        if candidate.group in used_groups or candidate in selected:
            continue
        selected.append(candidate)
        used_groups.add(candidate.group)

    selected.sort(key=lambda item: item.score, reverse=True)
    first_official = next((item for item in selected if item.official), None)
    if first_official is not None:
        selected.remove(first_official)
        selected.insert(0, first_official)
    return [candidate.item for candidate in selected[:limit]]


def _latest_version_subquery():
    return (
        select(
            ContentVersion.raw_content_id.label("rid"),
            func.max(ContentVersion.version).label("version"),
        )
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )


async def _review_candidates(
    session: AsyncSession, ids: list[int], tracked_products: list[dict], *, force: bool = False
) -> list[dict]:
    latest = _latest_version_subquery()
    rows = list(
        (
            await session.execute(
                select(RawContent, ContentVersion, Source)
                .join(latest, latest.c.rid == RawContent.id)
                .join(
                    ContentVersion,
                    (ContentVersion.raw_content_id == latest.c.rid)
                    & (ContentVersion.version == latest.c.version),
                )
                .join(Source, RawContent.source_id == Source.id, isouter=True)
                .where(RawContent.id.in_(ids))
                .order_by(RawContent.id)
            )
        ).all()
    )
    metrics = await _latest_metrics(session, ids)
    # 词表按分区加载：审核提示词与已知词都限定在内容所属分区内
    communities = {getattr(raw, "community", None) or DEFAULT_COMMUNITY for raw, _, _ in rows}
    terms_by_community = {
        community: await _load_known_terms(session, community) for community in communities
    }
    candidates = []
    reviews = {row.raw_content_id: row for row in await session.scalars(
        select(ContentReview).where(ContentReview.raw_content_id.in_(ids))
    )}
    for raw, version, source in rows:
        review = reviews.get(raw.id)
        if review is not None and not force:
            if get_community(raw.community).domain == "news":
                news = review.news or {}
                if news.get("content_hash") == raw.content_hash and news.get("content_version") == version.version:
                    continue
            elif review.updated_at >= version.parsed_at:
                continue
        normalized = version.normalized_json or {}
        media = normalized.get("media") or []
        cover = next((item for item in media if isinstance(item, str) and item), None)
        text_plain = _plain(version.text or "")
        community = getattr(raw, "community", None) or DEFAULT_COMMUNITY
        candidates.append(
            {
                "id": raw.id,
                "platform": raw.platform,
                "community": community,
                "content_hash": raw.content_hash,
                "content_version": version.version,
                "identity": source.identity if source else "unknown",
                "title": (version.title or raw.external_id)[:240],
                "text": text_plain[:3000] if get_community(community).domain == "news" else text_plain[:1200],
                "rule_category": version.category,
                "metrics": metrics.get(raw.id),
                "cover": None if get_community(community).domain == "news" else cover,
                "matched_keywords": (raw.matched_keywords or [])[:12],
                "known_terms": terms_by_community[community],
                "product_context": _match_product_context(
                    tracked_products,
                    community,
                    version.title or "",
                    text_plain,
                    raw.matched_keywords or [],
                ),
            }
        )
    return candidates


def _match_product_context(
    tracked_products: list[dict], community: str, title: str, text: str, matched_keywords: list
) -> dict | None:
    """内容命中所属分区在售商品的匹配词时，附带商品上下文供 AI 做履约预判。"""
    haystack = f"{title} {text} {' '.join(str(k) for k in matched_keywords)}"
    for product in tracked_products:
        if (product.get("community") or DEFAULT_COMMUNITY) != community:
            continue
        hits = [term for term in product["terms"] if term and term in haystack]
        if hits:
            return {"name": product["name"], "matched_terms": hits[:6]}
    return None


async def _load_tracked_products(session: AsyncSession) -> list[dict]:
    rows = await session.execute(select(Product.id, Product.name, Product.watch_terms, Product.community))
    products = []
    for pid, name, terms, community in rows:
        term_list = [t for t in (terms or []) if isinstance(t, str) and len(t.strip()) >= 2]
        if term_list:
            products.append(
                {"id": pid, "name": name, "terms": term_list, "community": community or DEFAULT_COMMUNITY}
            )
    return products


async def _load_known_terms(session: AsyncSession, community: str = DEFAULT_COMMUNITY) -> list[str]:
    """权威相关词表：所属分区的采集目标关键词 + 适用的分类词典。

    模型知识库不认识最新的角色/活动，词表是相关性的判据来源，
    管理员在词典里补录新词即可让审核跟上版本更新。
    """
    target_rows = await session.execute(
        select(CollectTarget.target_value).where(
            CollectTarget.enabled.is_(True),
            CollectTarget.target_type == "keyword_search",
            CollectTarget.community == community,
        )
    )
    rule_rows = await session.execute(
        select(ClassificationRule.pattern).where(
            ClassificationRule.enabled.is_(True),
            # community 为空的规则全分区通用
            (ClassificationRule.community.is_(None)) | (ClassificationRule.community == community),
        )
    )
    terms: list[str] = []
    seen: set[str] = set()
    for (value,) in (*target_rows, *rule_rows):
        term = (value or "").strip()
        if len(term) >= 2 and term not in seen:
            seen.add(term)
            terms.append(term)
        if len(terms) >= 80:
            break
    return terms


async def _latest_metrics(session: AsyncSession, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    latest = (
        select(
            MetricSnapshot.raw_content_id.label("rid"),
            func.max(MetricSnapshot.id).label("snapshot_id"),
        )
        .where(MetricSnapshot.raw_content_id.in_(ids))
        .group_by(MetricSnapshot.raw_content_id)
        .subquery()
    )
    rows = await session.execute(
        select(MetricSnapshot).join(latest, MetricSnapshot.id == latest.c.snapshot_id)
    )
    return {item.raw_content_id: item.metrics for item in rows.scalars()}


async def _event_groups(session: AsyncSession, ids: list[int]) -> dict[int, int]:
    rows = await session.execute(
        select(Evidence.raw_content_id, Evidence.event_id)
        .where(Evidence.raw_content_id.in_(ids), Evidence.role != "excluded")
        .order_by(Evidence.id)
    )
    groups: dict[int, int] = {}
    for content_id, event_id in rows:
        groups.setdefault(content_id, event_id)
    return groups


def _parse_review(
    response: str, content_id: int, product_context_present: bool = False
) -> dict | None:
    """解析单条审核判定；接受 {"review": {...}} 或 {"reviews": [{...}]} 两种形状。"""
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        return None
    item = data.get("review")
    if item is None and isinstance(data.get("reviews"), list):
        for candidate in data["reviews"]:
            if isinstance(candidate, dict) and int(candidate.get("id") or 0) == content_id:
                item = candidate
                break
    if not isinstance(item, dict):
        return None
    relevant = item.get("relevant")
    promotional = item.get("promotional")
    violation = item.get("violation")
    if not isinstance(relevant, bool) or not isinstance(promotional, bool):
        return None
    violation = bool(violation) if isinstance(violation, bool) else False
    category = item.get("category_hint")
    if category not in _ALLOWED_CATEGORIES:
        category = None
    links: list[dict] = []
    for link in (item.get("links") or [])[:12]:
        if isinstance(link, str):
            link = {"url": link}
        if not isinstance(link, dict):
            continue
        # 模型可能照抄正文里的 HTML 实体（&amp;），还原为真实 URL
        url = unescape(str(link.get("url") or "").strip())
        label = unescape(str(link.get("label") or "").strip()[:40])
        if url.startswith(("http://", "https://")):
            links.append({"url": url, "label": label})
    parsed_fulfillment = None
    fulfillment = item.get("fulfillment")
    if isinstance(fulfillment, dict):
        # 模型常用类目词（unboxing/arrived 等）描述履约类型，映射回标准 kind
        kind_alias = {
            "unboxing": "self_purchase", "arrived": "self_purchase",
            "received": "self_purchase", "order": "self_purchase", "self": "self_purchase",
            "sample": "pr_sample", "seeded": "pr_sample",
            "repost": "forward", "video": "forward",
            "normal": "none", "other": "none", "unknown": "none",
        }
        kind = str(fulfillment.get("kind") or "").strip().lower()
        kind = kind_alias.get(kind, kind)
        if kind in {"self_purchase", "pr_sample", "forward", "none"}:
            parsed_fulfillment = {
                "kind": kind,
                "confidence": max(0, min(100, int(fulfillment.get("confidence") or 0))),
                "reason": str(fulfillment.get("reason") or "")[:80],
            }
    if parsed_fulfillment is None and product_context_present:
        # 小模型常丢尾部字段：从它自己的类目与理由做确定性推导（宁低置信不下架判断）
        reason_text = str(item.get("reason") or "")
        if category in {"unboxing", "arrived"}:
            # 先看自购正向词（理由里"非官方送测"这类否定语境不作为送测依据）
            if any(term in reason_text for term in ("自购", "订单", "入手", "自己买")):
                kind = "self_purchase"
            elif any(term in reason_text for term in ("送测", "样品", "寄送", "官方提供")):
                kind = "pr_sample"
            else:
                kind = "self_purchase"  # 开箱/到货类目默认自购概率更高
            parsed_fulfillment = {"kind": kind, "confidence": 60, "reason": reason_text[:80] or "开箱/到货类目候选"}
        elif category == "quality":
            parsed_fulfillment = {"kind": "none", "confidence": 30, "reason": "质量反馈，履约方式未知"}
    return {
        "relevant": relevant,
        "promotional": promotional,
        "violation": violation,
        "quality_score": max(0, min(100, int(item.get("quality_score") or 0))),
        "category_hint": category,
        "reason": str(item.get("reason") or "")[:500],
        "links": links,
        "fulfillment": parsed_fulfillment,
        "news": parse_news(item.get("news")),
    }


def _views(metrics: dict | None) -> int:
    values = metrics or {}
    return int(values.get("view") or values.get("views") or values.get("view_count") or 0)


def _title_key(title: str) -> str:
    value = re.sub(r"[\W_《》【】]+", "", title.casefold())
    return value[:40] or title.casefold()[:40]


def _plain(value: str) -> str:
    import bleach

    return re.sub(r"\s+", " ", bleach.clean(value, tags=[], strip=True)).strip()


def _excerpt(value: str) -> str | None:
    text = _plain(value)
    if not text:
        return None
    return text[:140] + ("…" if len(text) > 140 else "")


def _review_system_prompt(community: str = DEFAULT_COMMUNITY) -> str:
    """审核提示词按分区模板化：游戏名与"其他游戏"示例取自 communities 注册表。"""
    meta = get_community(community)
    if meta.domain == "news":
        from app.services.ai_news import NEWS_REVIEW_PROMPT

        return NEWS_REVIEW_PROMPT
    game, other_games = f"《{meta.bare_name}》", meta.other_games_examples
    return f"""你是{game}主题情报站的编辑兼内容安全审核员。本站对{game}及其周边生态做定向采集，你的知识库可能不包含最新的角色、版本与活动——判断相关性必须以下面两条为准，不得依赖你自己认识与否：

1. 内容自带的 matched_keywords 是它被采集入库的原因（来自站点配置的采集关键词）。
2. 输入里的 known_terms 是管理员维护的"已确认相关词表"（含最新角色/系统/活动名）。标题、正文或封面命中词表中任一词，即视为相关（relevant=true）。

输出 JSON 的每个字段都必须包含（product_context 存在时 fulfillment 不可省略）：
{{"review":{{"relevant":true,"promotional":false,"violation":false,"quality_score":80,"category_hint":"version_update","reason":"简短依据","fulfillment":{{"kind":"none","confidence":30,"reason":"不涉及商品履约"}}}}}}。

规则：
- relevant=false 仅用于内容明确属于其他游戏/产品{other_games}且与{game}生态无关联的情形；绝不因"不认识某角色/某活动/某梗"而判 false。身份 identity 为 official 的内容来自已确认的官方账号，除非违规，relevant 恒为 true。
- promotional 表示抽奖、签到、拉互动、中奖名单或没有实质信息的推广动态。
- violation 表示违规：色情低俗、政治敏感、赌博诈骗、钓鱼链接、严重不实谣言或引战辱骂等不适合公开展示的内容；判定要克制，仅对明显违规给 true。
- quality_score 为 0 到 100 的编辑价值：官方版本、角色、活动、维护和实质公告优先；社区内容重视明确主题、信息量和独立表达，播放量只代表热度。
- category_hint 只能是 official_announce/version_update/presale/balance/delay/shipping/arrived/unboxing/review/quality/promotion/chat/other 或 null。
- links：从正文中提取的站外链接数组，只收有实际内容的页面（网页活动、H5 小游戏、奖励领取、新闻详情等），忽略社媒主页、下载页、官网首页和视频页；每项 {{"url":"原文中的完整链接","label":"不超过 12 字的简体中文名称，说明该链接是什么（如 B站主题收藏集、H5 签到活动）"}}；没有则返回空数组 []。label 必须是简体中文。
- 若输入包含 product_context（内容命中了在售联名商品的匹配词），必须输出商品履约预判字段：
  "fulfillment":{{"kind":"...","confidence":0-100,"reason":"不超过 60 字的简体中文依据"}}，缺省该字段视为输出错误。
  kind 只能是 self_purchase（自购开箱/到货晒单/订单截图）、pr_sample（官方送测/样品/共创）、forward（转发剪辑/非实拍内容）、none（普通资讯，与商品履约无关）。
  依据要点：实拍图、订单或收货信息、开箱时间线指向 self_purchase；提到送测、样品、官方寄送、共创指向 pr_sample；明显转发他人视频或二剪指向 forward；拿不准时 confidence 不超过 40。
- 若输入附带封面图，须结合图片与文字综合判断：图片低俗或违规记 violation=true；图文完全无关可作为参考，但相关性仍按词表规则定。不得补充外部事实。"""
