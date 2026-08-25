"""采集编排：一条 collect_target 任务的完整生命周期。

worker 与 Admin API 的"手动触发"都走这里；职责边界：
collector 只拿数据，ingest 只管入库，本模块负责任务记录、
平台礼貌间隔、健康状态与失败退避（"没抓到"必须可见）。
"""

import logging
import random
from datetime import datetime, timedelta

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.collectors.base import CollectorError, platform_slot
from app.collectors.bilibili import BilibiliCollector
from app.collectors.kuro import KuroCollector
from app.collectors.miyoushe import MiyousheCollector
from app.collectors.youtube_rss import YoutubeRssCollector
from app.config import get_settings
from app.db import SessionLocal
from app.models import CollectRun, CollectTarget, Platform, RunStatus
from app.services.ingest import IngestStats, ingest_items

logger = logging.getLogger(__name__)

# platform → 零参工厂；kuro 两个区域共享类但实例需指定 region
_COLLECTORS = {
    "bilibili": lambda: BilibiliCollector(),
    "miyoushe": lambda: MiyousheCollector(),
    "youtube": lambda: YoutubeRssCollector(),
    "kuro_cn": lambda: KuroCollector(Platform.KURO_CN),
    "kuro_global": lambda: KuroCollector(Platform.KURO_GLOBAL),
}


class CollectError(RuntimeError):
    pass


def filter_relevant(items: list[dict], target: CollectTarget) -> tuple[list[dict], int]:
    """采集相关性过滤：共现词（标题需至少命中一个）+ 热度门槛。

    返回 (保留列表, 过滤数量)。官方账号类目标（notices/dynamics/videos）
    不做过滤——官方内容本身就是信号。
    """
    if target.target_type == "notices":
        return items, 0
    co_terms = [t for t in (target.co_terms or []) if t]
    min_views = target.min_views or 0
    kept: list[dict] = []
    filtered = 0
    for item in items:
        # 社区视频简介常带整段频道标签或往期游戏名，拿简介匹配会把其他
        # 游戏的视频误判为鸣潮信息；标题信号更精确，也与后台字段说明一致。
        haystack = str(item.get("title") or "").casefold()
        hit_terms = [t for t in co_terms if t.casefold() in haystack]
        if co_terms and not hit_terms:
            filtered += 1
            continue
        if min_views > 0:
            views = int(
                item.get("view_count")
                or item.get("view")
                or (item.get("statistics") or {}).get("view")
                or 0
            )
            if views < min_views:
                filtered += 1
                continue
        # 关键词溯源：目标关键词 + 命中的共现词（多词共同命中一起记录）
        keywords = [target.target_value] if target.target_type == "keyword_search" else [
            target.display_name or target.target_value
        ]
        item["_matched_keywords"] = sorted({*keywords, *hit_terms})
        kept.append(item)
    return kept, filtered


async def run_collection(redis, target_id: int) -> dict:
    """执行一次采集。redis 为 arq Redis 池（复用连接，也用于平台间隔）。"""
    settings = get_settings()
    async with SessionLocal() as session:
        target = await session.get(CollectTarget, target_id)
        if target is None:
            raise CollectError(f"collect target {target_id} not found")

        run = CollectRun(target_id=target.id, started_at=utcnow(), status=RunStatus.RUNNING)
        session.add(run)
        await session.commit()

        stats: IngestStats | None = None
        error_text: str | None = None
        try:
            collector_factory = _COLLECTORS.get(target.platform)
            if collector_factory is None:
                raise CollectError(f"no collector for platform {target.platform}")
            async with platform_slot(
                redis, target.platform, settings.platform_min_interval_seconds
            ):
                fetch_result = await collector_factory().fetch(
                    target.target_type, target.target_value, settings.collect_limit
                )
            fetch_result.items, filtered_out = filter_relevant(fetch_result.items, target)
            if not filtered_out and target.target_type in ("keyword_search", "account_videos", "account_dynamics"):
                # 未配置过滤时也打关键词溯源标
                label = (
                    [target.target_value] if target.target_type == "keyword_search"
                    else [target.display_name or target.target_value]
                )
                for item in fetch_result.items:
                    item.setdefault("_matched_keywords", label)
            if filtered_out:
                logger.info(
                    "target %s: %s items filtered by relevance rules",
                    target.id, filtered_out,
                )
            stats = await ingest_items(session, fetch_result.fetch_kind, fetch_result.items)
            match_stats = None
            if target.platform in ("bilibili", "youtube") and (
                stats.touched_content_ids or target.platform == "youtube"
            ):
                from app.services.similarity import match_official_contents

                try:
                    match_stats = await match_official_contents(
                        session, stats.touched_content_ids or None
                    )
                    logger.info(
                        "official matching: candidates=%s decisions=%s positives=%s failed=%s",
                        match_stats.candidates,
                        match_stats.decisions,
                        match_stats.positive,
                        match_stats.failed_batches,
                    )
                except Exception:  # noqa: BLE001 - AI 匹配不得阻断采集主链路
                    logger.exception("official content matching failed")
            if stats.items_new or stats.items_updated or (match_stats and match_stats.decisions):
                # 分类 + 事件聚合跟随采集执行（全量 ensure，量级小）
                from app.services.events import run_aggregation

                agg = await run_aggregation(session)
                await session.commit()
                logger.info(
                    "aggregation: events_created=%s evidences=%s revisions=%s stages=%s",
                    agg.events_created, agg.evidences_added, agg.revisions_written, agg.stage_advances,
                )
                if agg.touched_event_ids and hasattr(redis, "enqueue_job"):
                    for event_id in agg.touched_event_ids:
                        await redis.enqueue_job("summarize_event", event_id)
                if target.platform == "youtube" and hasattr(redis, "enqueue_job"):
                    for content_id in stats.touched_content_ids:
                        await redis.enqueue_job("translate_content", content_id)
        except Exception as exc:  # noqa: BLE001 - 任何失败都落到健康状态里
            error_text = f"{type(exc).__name__}: {exc}"
            logger.error("collect target %s failed: %s", target_id, error_text)

        now = utcnow()
        target.last_run_at = now
        run.finished_at = now
        run.items_found = stats.items_found if stats else 0
        run.items_new = stats.items_new if stats else 0

        if stats is not None:
            run.status = RunStatus.SUCCESS if stats.parse_failed == 0 else RunStatus.PARTIAL
            target.last_success_at = now
            target.consecutive_failures = 0
            target.last_error = "; ".join(stats.errors[:3]) or None
        else:
            run.status = RunStatus.FAILED
            run.error = error_text
            target.consecutive_failures += 1
            target.last_error = error_text
        await session.commit()
        return {
            "run_id": run.id,
            "status": run.status,
            "items_found": run.items_found,
            "items_new": run.items_new,
            "error": error_text,
        }


def next_run_with_jitter(target: CollectTarget, now: datetime | None = None) -> datetime:
    """下次运行时间 = 间隔 × (1 ± jitter)；失败时按 2^n 退避（封顶 scheduler_failure_backoff_max）。"""
    settings = get_settings()
    now = now or utcnow()
    backoff = 1.0
    if target.consecutive_failures > 0:
        backoff = float(
            2 ** min(target.consecutive_failures, settings.scheduler_failure_backoff_max)
        )
    base = target.interval_minutes * 60 * backoff
    jitter = random.uniform(
        1 - settings.scheduler_jitter_ratio, 1 + settings.scheduler_jitter_ratio
    )
    return now + timedelta(seconds=base * jitter)


async def get_redis_pool():
    """arq 任务投递用的 Redis 池（Admin API 手动触发、调度器共用）。"""
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


async def load_due_targets(session: AsyncSession, now, limit: int = 50) -> list[CollectTarget]:
    result = await session.execute(
        select(CollectTarget)
        .where(
            CollectTarget.enabled.is_(True),
            (CollectTarget.next_run_at.is_(None)) | (CollectTarget.next_run_at <= now),
        )
        .order_by(CollectTarget.next_run_at.asc())
        .limit(limit)
    )
    return list(result.scalars())
