"""首页精选：规则缩小候选，AI 评估相关性与编辑价值，公开接口稳定回退。"""

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import (
    ContentReview,
    ContentVersion,
    Evidence,
    MetricSnapshot,
    RawContent,
    Source,
)
from app.models.enums import Category
from app.services.ai import ai_available, chat, get_ai_config, record_usage
from app.services.classify import HIDDEN_CATEGORIES

REVIEW_BATCH_SIZE = 10
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


async def review_contents(session: AsyncSession, content_ids: list[int]) -> ReviewStats:
    """批量保存 AI 候选判断；模型失败时保留旧结果并让首页走规则回退。"""
    stats = ReviewStats()
    ids = list(dict.fromkeys(int(item) for item in content_ids if item))
    if not ids:
        return stats
    config = await get_ai_config(session)
    if not ai_available(config):
        stats.skipped_reason = "ai_not_configured"
        return stats

    candidates = await _review_candidates(session, ids)
    stats.candidates = len(candidates)
    for start in range(0, len(candidates), REVIEW_BATCH_SIZE):
        batch = candidates[start : start + REVIEW_BATCH_SIZE]
        allowed_ids = {item["id"] for item in batch}
        started = time.monotonic()
        usage = None
        try:
            raw_response, usage = await chat(
                config,
                _REVIEW_SYSTEM_PROMPT,
                json.dumps({"contents": batch}, ensure_ascii=False),
                json_mode=True,
            )
            decisions = _parse_reviews(raw_response, allowed_ids)
            if not decisions:
                raise ValueError("model returned no valid reviews")
        except Exception as exc:  # noqa: BLE001 - 精选失败不阻断采集和公开页
            stats.failed_batches += 1
            logger.warning("content review failed for batch starting %s: %r", batch[0]["id"], exc)
            await record_usage(
                session,
                task="review_content",
                config=config,
                usage=usage,
                duration_ms=int((time.monotonic() - started) * 1000),
                ref_id=batch[0]["id"],
                status="failed",
            )
            continue

        await record_usage(
            session,
            task="review_content",
            config=config,
            usage=usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            ref_id=batch[0]["id"],
        )
        for content_id, decision in decisions.items():
            row = await session.get(ContentReview, content_id)
            if row is None:
                row = ContentReview(raw_content_id=content_id, model=config["model"])
                session.add(row)
            row.relevant = decision["relevant"]
            row.promotional = decision["promotional"]
            row.quality_score = decision["quality_score"]
            row.category_hint = decision["category_hint"]
            row.duplicate_of_raw_id = decision["duplicate_of"]
            row.reason = decision["reason"]
            row.model = config["model"]
            row.updated_at = utcnow()
            stats.reviewed += 1
        await session.commit()
    return stats


async def review_recent_contents(
    session: AsyncSession, *, limit: int = 40, only_missing: bool = True
) -> ReviewStats:
    """重算近期带首图内容，供部署回填和管理端手动触发。"""
    latest = _latest_version_subquery()
    query = (
        select(RawContent.id, ContentVersion.normalized_json, ContentReview.raw_content_id)
        .join(latest, latest.c.rid == RawContent.id)
        .join(
            ContentVersion,
            (ContentVersion.raw_content_id == latest.c.rid)
            & (ContentVersion.version == latest.c.version),
        )
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
    for content_id, normalized, reviewed_id in rows:
        if only_missing and reviewed_id is not None:
            continue
        media = (normalized or {}).get("media") or []
        if media:
            ids.append(content_id)
        if len(ids) >= limit:
            break
    return await review_contents(session, ids)


async def select_highlights(session: AsyncSession, limit: int = 5) -> list[dict]:
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
            .where(publication_time >= utcnow() - HIGHLIGHT_WINDOW)
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


async def _review_candidates(session: AsyncSession, ids: list[int]) -> list[dict]:
    latest = _latest_version_subquery()
    rows = await session.execute(
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
    metrics = await _latest_metrics(session, ids)
    return [
        {
            "id": raw.id,
            "platform": raw.platform,
            "identity": source.identity if source else "unknown",
            "title": (version.title or raw.external_id)[:240],
            "text": _plain(version.text or "")[:1200],
            "rule_category": version.category,
            "metrics": metrics.get(raw.id),
        }
        for raw, version, source in rows
    ]


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


def _parse_reviews(response: str, allowed_ids: set[int]) -> dict[int, dict]:
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    data = json.loads(cleaned)
    result = {}
    for item in data.get("reviews") or []:
        content_id = int(item.get("id") or 0)
        if content_id not in allowed_ids:
            continue
        relevant = item.get("relevant")
        promotional = item.get("promotional")
        if not isinstance(relevant, bool) or not isinstance(promotional, bool):
            continue
        category = item.get("category_hint")
        if category not in _ALLOWED_CATEGORIES:
            category = None
        duplicate = item.get("duplicate_of")
        duplicate = int(duplicate) if duplicate else None
        if duplicate not in allowed_ids or duplicate == content_id:
            duplicate = None
        result[content_id] = {
            "relevant": relevant,
            "promotional": promotional,
            "quality_score": max(0, min(100, int(item.get("quality_score") or 0))),
            "category_hint": category,
            "duplicate_of": duplicate,
            "reason": str(item.get("reason") or "")[:500],
        }
    return result


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


_REVIEW_SYSTEM_PROMPT = """你是《鸣潮》情报编辑，只能依据输入内容输出 JSON：
{"reviews":[{"id":1,"relevant":true,"promotional":false,"quality_score":80,"category_hint":"version_update","duplicate_of":null,"reason":"简短依据"}]}。
必须逐项返回所有输入 id。relevant 表示内容明确与《鸣潮》或目标商品有关；promotional 表示抽奖、签到、拉互动、中奖名单或没有实质信息的推广动态。
quality_score 为 0 到 100 的编辑价值：官方版本、角色、活动、维护和实质公告优先；社区内容重视明确主题、信息量和独立表达，播放量只代表热度。
category_hint 只能是 official_announce/version_update/presale/balance/delay/shipping/arrived/unboxing/review/quality/promotion/chat/other 或 null。
只有输入批次中两条内容明确属于同一发布物或同一事件时才填写 duplicate_of；同角色、同版本但主题不同不能判重复。不得补充外部事实。"""
