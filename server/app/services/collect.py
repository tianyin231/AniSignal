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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import DEFAULT_COMMUNITY
from app.collectors.base import CollectorError, platform_slot
from app.collectors.bilibili import BilibiliCollector
from app.collectors.kuro import KuroCollector
from app.collectors.miyoushe import MiyousheCollector
from app.collectors.youtube_api import YoutubeApiCollector
from app.collectors.youtube_rss import YoutubeRssCollector
from app.config import get_settings
from app.db import SessionLocal
from app.models import CollectRun, CollectTarget, Platform, RunStatus
from app.services.ingest import IngestStats, ingest_items
from app.services.intent import persist_intent_tiers, triage_hits

logger = logging.getLogger(__name__)

# platform → 零参工厂；kuro 两个区域共享类但实例需指定 region
_COLLECTORS = {
    "bilibili": lambda: BilibiliCollector(),
    "miyoushe": lambda: MiyousheCollector(),
    "youtube": lambda: (YoutubeApiCollector() if get_settings().youtube_api_key else YoutubeRssCollector()),
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
    if getattr(target, "tier", "general") == "core":
        # 核心关键词目标（商品/联名等特定内容）：豁免共现词；
        # min_views > 0 时仍作为兜底门槛（管理员可为核心词设下限）
        kept = []
        co_terms = [t for t in (target.co_terms or []) if t]
        min_views = target.min_views or 0
        for item in items:
            keywords = [target.target_value] if target.target_type == "keyword_search" else [
                target.display_name or target.target_value
            ]
            haystack = str(item.get("title") or "").casefold()
            hit_terms = [t for t in co_terms if t.casefold() in haystack]
            item["_matched_keywords"] = sorted({*keywords, *hit_terms})
            views = int(
                item.get("view_count")
                or item.get("view")
                or (item.get("statistics") or {}).get("view")
                or 0
            )
            if min_views > 0 and views < min_views:
                continue
            kept.append(item)
        return kept, 0
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


async def _backfill_empty_draw_dynamics(
    session: AsyncSession, collector, limit: int = 5
) -> int:
    """自愈回填：为匿名详情补全曾瞬时失败的空 DRAW 动态重试。

    成功的动态以新版本入库（保留原 author/统计），空卡片的琥珀提示随之消失。
    """
    import json as _json

    cutoff = utcnow() - timedelta(days=7)
    rows = (await session.execute(text(
        "SELECT rc.id, rc.external_id, rc.raw_json, rc.community "
        "FROM raw_contents rc JOIN content_versions cv ON cv.raw_content_id=rc.id "
        "AND cv.version=(SELECT MAX(version) FROM content_versions WHERE raw_content_id=rc.id) "
        "WHERE rc.content_type='dynamic' AND rc.platform='bilibili' "
        "AND COALESCE(LENGTH(cv.text),0)=0 "
        "AND rc.collected_at > :cutoff "
        "ORDER BY rc.id DESC LIMIT :lim"
    ), {"cutoff": cutoff, "lim": limit})).fetchall()
    if not rows:
        return 0

    enriched_by_community: dict[str, list[dict]] = {}
    touched: list[int] = []
    for rid, external_id, raw_json, community in rows:
        if not external_id:
            continue
        try:
            rich = await collector._fetch_draw_detail(external_id)
        except Exception:  # noqa: BLE001 - 单条失败跳过
            continue
        if not ((rich.get("text") or "").strip() or rich.get("pics")):
            continue
        raw = raw_json or {}
        if isinstance(raw, str):
            import json as _json

            try:
                raw = _json.loads(raw)
            except ValueError:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        item = dict(raw)
        item.update({"id": external_id, "text": rich.get("text") or "", "pics": rich.get("pics") or []})
        enriched_by_community.setdefault(community or DEFAULT_COMMUNITY, []).append(item)
        touched.append(rid)
    total = 0
    for community, items in enriched_by_community.items():
        await ingest_items(session, "bilibili_dynamics", items, community=community)
        total += len(items)
    if total:
        logger.info(
            "backfilled %s empty DRAW dynamics with anonymous detail: %s",
            total, touched,
        )
    return total


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
        items_fetched = 0
        items_filtered = 0
        items_ignored = 0
        triage = None
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
            items_fetched = len(fetch_result.items)
            fetch_result.items, filtered_out = filter_relevant(fetch_result.items, target)
            items_filtered = filtered_out
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
            # 意图打标（目标配了自然语言意图才走 AI）：parser 归一化命中 → 三档判定
            # → 忽略的丢弃、一般的入库、重要的入库并落 tier。AI 不可用时全部放行。
            try:
                triage = await triage_hits(
                    session, target, fetch_result.items, fetch_result.fetch_kind
                )
            except Exception:  # noqa: BLE001 - 打标不得阻断采集主链路
                logger.exception("intent triage failed for target %s", target.id)
            if triage is not None:
                items_ignored = triage.ignored
                if triage.labeled:
                    fetch_result.items = triage.kept
                    logger.info(
                        "target %s: %s items ignored by intent triage",
                        target.id, triage.ignored,
                    )
            stats = await ingest_items(
                session, fetch_result.fetch_kind, fetch_result.items, community=target.community
            )
            important_content_ids: list[int] = []
            if triage is not None and triage.labeled:
                # 三档结果写回内容（AI 层产物，可重算）；important 的留给机器人投递
                tier_by_external = await persist_intent_tiers(
                    session, target.platform, triage.hits
                )
                important_content_ids = [
                    tier_by_external[external_id]
                    for external_id in triage.important_ids
                    if external_id in tier_by_external
                ]
                if important_content_ids:
                    from app.services.bot import push_important_contents

                    try:
                        pushed = await push_important_contents(session, important_content_ids)
                        logger.info(
                            "intent push: %s critical deliveries for %s important contents",
                            pushed, len(important_content_ids),
                        )
                    except Exception:  # noqa: BLE001 - 投递失败不影响采集结果
                        logger.exception("push important contents failed")
            if fetch_result.fetch_kind == "bilibili_dynamics":
                try:
                    await _backfill_empty_draw_dynamics(session, collector_factory())
                except Exception:  # noqa: BLE001 - 回填失败不影响本轮采集
                    logger.warning("empty DRAW backfill failed", exc_info=True)
            match_stats = None
            if stats.touched_content_ids:
                from app.services.similarity import match_official_contents

                try:
                    match_stats = await match_official_contents(
                        session, stats.touched_content_ids
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
                if target.platform in ("youtube", "kuro_global") and hasattr(redis, "enqueue_job"):
                    for content_id in stats.touched_content_ids:
                        await redis.enqueue_job("translate_content", content_id)
                if stats.touched_content_ids and hasattr(redis, "enqueue_job"):
                    await redis.enqueue_job("review_contents", stats.touched_content_ids)
        except Exception as exc:  # noqa: BLE001 - 任何失败都落到健康状态里
            error_text = f"{type(exc).__name__}: {exc}"
            logger.error("collect target %s failed: %s", target_id, error_text)

        now = utcnow()
        target.last_run_at = now
        run.finished_at = now
        run.items_found = stats.items_found if stats else 0
        run.items_new = stats.items_new if stats else 0
        run.items_fetched = items_fetched
        run.items_filtered = items_filtered
        run.items_ignored = items_ignored
        run.items_updated = stats.items_updated if stats else 0
        run.parse_failed = stats.parse_failed if stats else 0
        run.official_items = stats.official_items if stats else 0
        run.community_items = stats.community_items if stats else 0
        run.duration_ms = max(0, int((now - run.started_at).total_seconds() * 1000))

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
        from app.services.statistics import record_collection_stat

        await record_collection_stat(session, run)
        await session.commit()
        if hasattr(redis, "delete"):
            try:
                await redis.delete(*(f"statistics:v1:{days}" for days in (7, 30, 90, 365)))
            except Exception:  # noqa: BLE001 - 缓存失效失败不阻断采集结果
                logger.warning("statistics cache invalidation failed", exc_info=True)
        return {
            "run_id": run.id,
            "status": run.status,
            "items_found": run.items_found,
            "items_new": run.items_new,
            "items_ignored": run.items_ignored,
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
