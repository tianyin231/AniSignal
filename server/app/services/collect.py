"""采集编排：一条 collect_target 任务的完整生命周期。

worker 与 Admin API 的"手动触发"都走这里；职责边界：
collector 只拿数据，ingest 只管入库，本模块负责任务记录、
平台礼貌间隔、健康状态与失败退避（"没抓到"必须可见）。
"""

import logging
import random
import re
import unicodedata
from datetime import datetime, timedelta

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import COMMUNITIES, DEFAULT_COMMUNITY, get_community
from app.collectors.base import CollectorDeferred, CollectorError, platform_slot
from app.collectors.bilibili import BilibiliCollector
from app.collectors.kuro import KuroCollector
from app.collectors.miyoushe import MiyousheCollector
from app.collectors.youtube_api import YoutubeApiCollector
from app.collectors.youtube_rss import YoutubeRssCollector
from app.collectors.x import XCollector
from app.config import get_settings
from app.db import SessionLocal
from app.models import CollectRun, CollectTarget, Platform, Product, RawContent, RunStatus
from app.services.ingest import IngestStats, ingest_items
from app.services.collector_proxy import fetch_with_fallback, configure_collector, redact_proxy
from app.services.queue import enqueue_review_jobs
from app.services.product_matching import matches_product_text

logger = logging.getLogger(__name__)

# platform → 零参工厂；kuro 两个区域共享类但实例需指定 region
_COLLECTORS = {
    "bilibili": lambda: BilibiliCollector(),
    "miyoushe": lambda: MiyousheCollector(),
    "youtube": lambda: (YoutubeApiCollector() if get_settings().youtube_api_key else YoutubeRssCollector()),
    "x": XCollector,
    "kuro_cn": lambda: KuroCollector(Platform.KURO_CN),
    "kuro_global": lambda: KuroCollector(Platform.KURO_GLOBAL),
}


class CollectError(RuntimeError):
    pass


def _source_alias_hit(title: str, alias: str) -> bool:
    title = unicodedata.normalize("NFKC", title).casefold()
    alias = unicodedata.normalize("NFKC", alias).casefold()
    # 不让 HSR 命中 longerhsrword；中文别名可直接连接角色名。
    prefix = r"(?<![a-z0-9])" if alias[:1].isascii() and alias[:1].isalnum() else ""
    suffix = r"(?![a-z0-9])" if alias[-1:].isascii() and alias[-1:].isalnum() else ""
    return bool(re.search(prefix + re.escape(alias) + suffix, title))


def youtube_title_communities(title: str) -> set[str]:
    """只按标题识别游戏，供共享频道目标做排他的分区匹配。"""
    return {
        meta.key for meta in COMMUNITIES.values() if meta.domain == "game"
        and any(_source_alias_hit(title, alias) for alias in (*meta.game_names, *meta.source_aliases))
    }


def filter_relevant(
    items: list[dict], target: CollectTarget, *, product_watch_terms: list[str] | None = None,
) -> tuple[list[dict], int]:
    """采集相关性过滤：共现词（标题需至少命中一个）+ 热度门槛。

    返回 (保留列表, 过滤数量)。官方账号类目标（notices/dynamics/videos）
    不做过滤——官方内容本身就是信号。
    """
    original_count = len(items)
    if product_watch_terms is not None:
        # 只过滤显式关联的商品目标。搜索引擎的宽泛相关结果不等于商品证据。
        items = [item for item in items if matches_product_text(
            str(item.get("title") or ""),
            str(item.get("text") or item.get("description") or item.get("desc") or item.get("summary") or ""),
            product_watch_terms,
        )]
    co_terms = [t for t in (target.co_terms or []) if t]
    if target.platform == "youtube" and co_terms:
        # raw_content 只能属于一个分区；多游戏标题不可由最先运行的目标抢归属。
        meta = get_community(target.community)
        aliases = (*meta.game_names, *meta.source_aliases)
        uses_game_terms = any(
            unicodedata.normalize("NFKC", term).casefold() == unicodedata.normalize("NFKC", alias).casefold()
            for term in co_terms for alias in aliases
        )
        if uses_game_terms:
            items = [item for item in items if
                youtube_title_communities(str(item.get("title") or "")) == {meta.key}
                # 同一系列的独立游戏不能借母系列名称混进来。
                and not any(_source_alias_hit(str(item.get("title") or ""), term) for term in meta.source_exclusions)
            ]
            co_terms = list(dict.fromkeys([*co_terms, *aliases]))
    if target.target_type == "notices":
        return items, original_count - len(items)
    is_news = get_community(target.community).domain == "news"
    if is_news:
        from app.services.ai_news import is_news_candidate
        from app.services import aihot

        kept = []
        for item in items:
            if target.platform == "x":
                # 英文原帖没有视频标题；全文交给资讯审核，不套中文标题词表。
                body = str(item.get("text") or "").casefold()
                if co_terms and not any(term.casefold() in body for term in co_terms):
                    continue
            elif not aihot.bridge_enabled() and not is_news_candidate(str(item.get("title") or "")):
                continue
            item["_matched_keywords"] = [target.target_value]
            kept.append(item)
        return kept, original_count - len(kept)
    if getattr(target, "tier", "general") == "core":
        # 核心关键词目标（商品/联名等特定内容）：豁免共现词；
        # min_views > 0 时仍作为兜底门槛（管理员可为核心词设下限）
        kept = []
        min_views = target.min_views or 0
        for item in items:
            keywords = [target.target_value] if target.target_type == "keyword_search" else [
                target.display_name or target.target_value
            ]
            haystack = str(item.get("title") or "").casefold()
            hit_terms = [t for t in co_terms if (
                _source_alias_hit(haystack, t) if target.platform == "youtube" else t.casefold() in haystack
            )]
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
        return kept, original_count - len(kept)
    min_views = target.min_views or 0
    kept: list[dict] = []
    filtered = original_count - len(items)
    for item in items:
        # 社区视频简介常带整段频道标签或往期游戏名，拿简介匹配会把其他
        # 游戏的视频误判为鸣潮信息；标题信号更精确，也与后台字段说明一致。
        haystack = str(item.get("title") or "").casefold()
        hit_terms = [t for t in co_terms if (
            _source_alias_hit(haystack, t) if target.platform == "youtube" else t.casefold() in haystack
        )]
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
    """回填缺失正文和图片的 DRAW/ARTICLE，跳过直播推荐和纯图片动态。

    成功的动态以新版本入库（保留原 author/统计），空卡片的琥珀提示随之消失。
    """
    import json as _json

    cutoff = utcnow() - timedelta(days=7)
    rows = (await session.execute(text(
        "SELECT rc.id, rc.external_id, rc.raw_json, rc.community "
        "FROM raw_contents rc JOIN content_versions cv ON cv.raw_content_id=rc.id "
        "AND cv.version=(SELECT MAX(version) FROM content_versions WHERE raw_content_id=rc.id) "
        "WHERE rc.content_type='dynamic' AND rc.platform='bilibili' "
        "AND JSON_EXTRACT(rc.raw_json, '$.type') IN ('DYNAMIC_TYPE_DRAW','DYNAMIC_TYPE_ARTICLE') "
        "AND COALESCE(LENGTH(cv.text),0)=0 "
        "AND COALESCE(JSON_EXTRACT(cv.normalized_json, '$.media'),'[]') IN ('[]','null') "
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
        run_id = run.id

        stats: IngestStats | None = None
        error_text: str | None = None
        postprocess_errors: list[str] = []
        retry_at: datetime | None = None
        items_fetched = 0
        items_filtered = 0
        proxy_url = None
        try:
            collector_factory = _COLLECTORS.get(target.platform)
            if collector_factory is None:
                raise CollectError(f"no collector for platform {target.platform}")
            collector = collector_factory()
            product = await session.scalar(select(Product).where(Product.monitor_target_id == target.id))
            is_news = get_community(target.community).domain == "news"
            if is_news and isinstance(collector, BilibiliCollector):
                collector.newest_first = True
            if isinstance(collector, XCollector):
                collector.redis = redis
            async def fetch():
                if isinstance(collector, XCollector):
                    return await collector.fetch(target.target_type, target.target_value, settings.collect_limit)
                async with platform_slot(redis, target.platform, settings.platform_min_interval_seconds):
                    return await collector.fetch(target.target_type, target.target_value, settings.collect_limit)
            fetch_result = await fetch_with_fallback(collector, session, target.platform, fetch)
            proxy_url = collector.proxy_url
            items_fetched = len(fetch_result.items)
            fetch_result.items, filtered_out = filter_relevant(
                fetch_result.items, target,
                product_watch_terms=product.watch_terms if product is not None else None,
            )
            if target.platform == "youtube" and fetch_result.items:
                # 标题后续改成另一游戏时，也不能经新分区目标更新已有异区证据。
                other_owned = set((await session.scalars(select(RawContent.external_id).where(
                    RawContent.platform == "youtube",
                    RawContent.external_id.in_([str(item.get("id") or "") for item in fetch_result.items]),
                    RawContent.community != target.community,
                ))).all())
                kept = [item for item in fetch_result.items if str(item.get("id") or "") not in other_owned]
                filtered_out += len(fetch_result.items) - len(kept)
                fetch_result.items = kept
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
            stats = await ingest_items(
                session, fetch_result.fetch_kind, fetch_result.items, community=target.community
            )
            if is_news and hasattr(redis, "enqueue_job"):
                from app.services.ai_news import pending_news_ids

                # 审核服务临时失败后，下轮仍能补审未完成的候选。
                external_ids = [str(item.get("bvid") or item.get("id") or "") for item in fetch_result.items]
                pending_ids = await pending_news_ids(session, external_ids=external_ids)
                if pending_ids:
                    await enqueue_review_jobs(redis, pending_ids)
            if fetch_result.fetch_kind == "bilibili_dynamics":
                try:
                    repair_collector = collector_factory()
                    configure_collector(repair_collector, proxy_url)
                    await _backfill_empty_draw_dynamics(session, repair_collector)
                except Exception as exc:  # noqa: BLE001 - 回填失败不影响本轮采集
                    postprocess_errors.append(f"dynamic backfill failed: {type(exc).__name__}: {exc}")
                    logger.warning("empty DRAW backfill failed", exc_info=True)
            match_stats = None
            from app.services import aihot
            engine_news = is_news and aihot.bridge_enabled()
            if stats.touched_content_ids and not engine_news:
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
                    if match_stats.failed_batches:
                        postprocess_errors.append(f"official content matching failed: {match_stats.failed_batches} batches")
                except Exception as exc:  # noqa: BLE001 - AI 匹配不得阻断采集主链路
                    postprocess_errors.append(f"official content matching failed: {type(exc).__name__}: {exc}")
                    logger.exception("official content matching failed")
            if not engine_news and (stats.items_new or stats.items_updated or (match_stats and match_stats.decisions)):
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
                if not is_news and stats.touched_content_ids and hasattr(redis, "enqueue_job"):
                    await enqueue_review_jobs(redis, stats.touched_content_ids)
        except CollectorDeferred as exc:
            retry_at = exc.retry_at
            error_text = str(exc)
            logger.info("collect target %s deferred until %s: %s", target_id, retry_at, exc)
        except Exception as exc:  # noqa: BLE001 - 任何失败都落到健康状态里
            error_text = f"{type(exc).__name__}: {redact_proxy(str(exc), proxy_url)}"
            logger.error("collect target %s failed: %s", target_id, error_text)
            # 入库已单独提交；后处理事务失败时先回滚，确保健康记录仍能落库。
            await session.rollback()
            target = await session.get(CollectTarget, target_id)
            run = await session.get(CollectRun, run_id)

        if not session.is_active:
            # 可选回填/匹配可能吞掉了数据库异常，但失败事务不能继续写健康记录。
            await session.rollback()
            target = await session.get(CollectTarget, target_id)
            run = await session.get(CollectRun, run_id)
        now = utcnow()
        target.last_run_at = now
        run.finished_at = now
        run.items_found = stats.items_found if stats else 0
        run.items_new = stats.items_new if stats else 0
        run.items_fetched = items_fetched
        run.items_filtered = items_filtered
        run.items_updated = stats.items_updated if stats else 0
        run.parse_failed = stats.parse_failed if stats else 0
        run.official_items = stats.official_items if stats else 0
        run.community_items = stats.community_items if stats else 0
        run.duration_ms = max(0, int((now - run.started_at).total_seconds() * 1000))

        errors = ([error_text] if error_text else []) + postprocess_errors + (stats.errors[:3] if stats else [])
        run.error = "; ".join(errors) or None
        all_parse_failed = bool(stats and stats.items_found and stats.parse_failed == stats.items_found)
        if retry_at is not None:
            run.status = RunStatus.SKIPPED
            # 冷却不累加失败；X 在冷却结束后随机恢复，避免所有账号同时到期。
            if target.platform == "x":
                target.next_run_at = max(
                    retry_at + timedelta(seconds=random.uniform(60, 600)),
                    next_run_with_jitter(target, now),
                )
            else:
                target.next_run_at = max(retry_at, now + timedelta(minutes=target.interval_minutes))
        elif stats is not None and not all_parse_failed:
            run.status = RunStatus.PARTIAL if errors or stats.parse_failed else RunStatus.SUCCESS
            target.last_success_at = now
            target.consecutive_failures = 0
            target.last_error = run.error
        else:
            run.status = RunStatus.FAILED
            target.consecutive_failures += 1
            target.last_error = run.error
        if retry_at is None:
            # 调度前的预约只防重复投递；完成后按本轮结果恢复周期或退避。
            target.next_run_at = next_run_with_jitter(target, now)
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
            "error": run.error,
            "retry_at": target.next_run_at if retry_at is not None else None,
        }


def next_run_with_jitter(target: CollectTarget, now: datetime | None = None) -> datetime:
    """每轮重新抽取间隔；动态风控定期探测恢复，其他失败按 2^n 退避。"""
    settings = get_settings()
    now = now or utcnow()
    backoff = 1.0
    if target.consecutive_failures > 0:
        backoff = float(
            2 ** min(target.consecutive_failures, settings.scheduler_failure_backoff_max)
        )
        if (target.platform == "bilibili" and target.target_type == "account_dynamics"
                and (target.last_error or "").startswith("BiliRiskControlError:")):
            # 匿名动态可能自行恢复；端点已有 30 分钟熔断，不再叠加 8–16 小时等待。
            # 保留管理员设置的长采集间隔。
            backoff = min(backoff, max(1.0, 60 / target.interval_minutes))
    base = target.interval_minutes * 60 * backoff
    if target.platform == "x":
        return now + timedelta(seconds=random.uniform(max(1800, base * 0.75), base * 1.25))
    jitter = random.uniform(
        1 - settings.scheduler_jitter_ratio, 1 + settings.scheduler_jitter_ratio
    )
    return now + timedelta(seconds=base * jitter)


async def recover_stale_runs(session: AsyncSession) -> int:
    """恢复超过一小时的遗留 running，避开仍在 30 分钟任务时限内的副本。"""
    from app.services.statistics import record_collection_stat

    runs = list(await session.scalars(
        select(CollectRun)
        .where(CollectRun.status == RunStatus.RUNNING, CollectRun.started_at < utcnow() - timedelta(hours=1))
        .with_for_update(skip_locked=True)
    ))
    for run in runs:
        run.status = RunStatus.FAILED
        run.error = "采集进程中断或任务超时（启动时恢复遗留记录）"
        # 用原任务的超时截止时间归档，不能把几周前的中断算进今天的失败率。
        run.finished_at = run.started_at + timedelta(minutes=30)
        await record_collection_stat(session, run)
    await session.commit()
    return len(runs)


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
