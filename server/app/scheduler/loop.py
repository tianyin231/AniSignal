"""调度器：常驻进程，按看板配置定时投递采集任务。

设计要点：
- 每 poll 秒扫一次到期目标（enabled && next_run_at <= now）；
- 投递前先预约 next_run_at（含抖动），避免在任务执行期间反复投递；
- 采集完成后按本轮结果重算 next_run_at，恢复成功不沿用旧失败退避。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.clock import BJ_TZ as _SHANGHAI, utcnow
from app.config import get_settings
from app.db import SessionLocal
from app.services.collect import get_redis_pool, load_due_targets, next_run_with_jitter

logger = logging.getLogger(__name__)

_local_morning_date = None
_local_reports_date = None
_local_custom_sweep_at = None
_local_fulfillment_sweep_at = None
_local_editorial_sweep_at = None
_local_product_monitor_at = None
_local_news_review_at = None
_local_extract_sweep_at = None
_CUSTOM_SWEEP_INTERVAL = timedelta(minutes=5)


def _morning_due(now: datetime) -> tuple[bool, str]:
    local = now.replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    return local.hour >= 8, local.date().isoformat()


def _reports_due(now: datetime) -> tuple[bool, str]:
    local = now.replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    return (local.hour > 8 or (local.hour == 8 and local.minute >= 30)), local.date().isoformat()


async def local_loop(queue) -> None:
    """本地测试模式的调度循环：不走 arq，直接投递到进程内 LocalQueue。"""
    settings = get_settings()
    logger.info("local scheduler started, poll=%.0fs", settings.scheduler_poll_seconds)
    global _local_morning_date
    while True:
        try:
            now = utcnow()
            async with SessionLocal() as session:
                due = await load_due_targets(session, now)
                for target in due:
                    target.next_run_at = next_run_with_jitter(target, now)
                    await session.commit()
                    await queue.enqueue_collect(target.id)
                    logger.info(
                        "local enqueued target %s (%s) next_run_at=%s",
                        target.id,
                        target.target_value,
                        target.next_run_at,
                    )
                due_now, day = _morning_due(now)
                from app.services.bot import run_bot_notifications
                await run_bot_notifications(session, now)
                await queue.enqueue_instant()
                if due_now and _local_morning_date != day:
                    await queue.enqueue_morning()
                    _local_morning_date = day
                    logger.info("local enqueued morning notifications for %s", day)
                global _local_reports_date
                reports_now, reports_day = _reports_due(now)
                if reports_now and _local_reports_date != reports_day:
                    await queue.enqueue_reports()
                    _local_reports_date = reports_day
                    logger.info("local enqueued periodic reports for %s", reports_day)
                global _local_custom_sweep_at
                global _local_editorial_sweep_at
                if _local_editorial_sweep_at is None or now >= _local_editorial_sweep_at:
                    await queue.enqueue_editorial()
                    _local_editorial_sweep_at = now + _CUSTOM_SWEEP_INTERVAL
                if _local_custom_sweep_at is None or now >= _local_custom_sweep_at:
                    await queue.enqueue_custom_sweep()
                    _local_custom_sweep_at = now + _CUSTOM_SWEEP_INTERVAL
                global _local_fulfillment_sweep_at
                if _local_fulfillment_sweep_at is None or now >= _local_fulfillment_sweep_at:
                    from app.services.fulfillment_review_sweep import enqueue_pending_fulfillment_reviews

                    result = await enqueue_pending_fulfillment_reviews(session, queue.redis, queue.enqueue_review)
                    _local_fulfillment_sweep_at = now + _CUSTOM_SWEEP_INTERVAL
                    logger.info("local fulfillment review sweep -> %s", result)
                global _local_product_monitor_at
                if _local_product_monitor_at is None or now >= _local_product_monitor_at:
                    from app.services.product_discovery import run_product_monitor_loop

                    result = await run_product_monitor_loop(session, now=now)
                    _local_product_monitor_at = now + _CUSTOM_SWEEP_INTERVAL
                    logger.info("local product monitor -> %s", result)
                global _local_news_review_at
                if _local_news_review_at is None or now >= _local_news_review_at:
                    from app.services.ai_news import pending_news_ids
                    from app.services.curation import missing_review_ids

                    news_ids = await pending_news_ids(session)
                    missing_ids = await missing_review_ids(session)
                    if news_ids or missing_ids:
                        await queue.enqueue_review(list(dict.fromkeys([*news_ids, *missing_ids])))
                    from app.services.content_brief import pending_brief_ids

                    brief_ids = await pending_brief_ids(session, limit=8)
                    if brief_ids:
                        await queue.enqueue_ai_batch("brief_content", brief_ids)
                    _local_news_review_at = now + _CUSTOM_SWEEP_INTERVAL
                global _local_extract_sweep_at
                if _local_extract_sweep_at is None or now >= _local_extract_sweep_at:
                    from app.services.extract import sweep_extractions
                    from app.services.redeem_codes import sweep_community_codes

                    await sweep_extractions(session)
                    await sweep_community_codes(session)
                    _local_extract_sweep_at = now + _CUSTOM_SWEEP_INTERVAL
        except Exception:  # noqa: BLE001 - 调度循环不能因单轮失败退出
            logger.exception("local scheduler tick failed")
        await asyncio.sleep(settings.scheduler_poll_seconds)


_LEADER_KEY = "anisignal:scheduler-leader"
_LEADER_TTL_MS = 90_000


async def _is_leader(redis) -> bool:
    """Redis 领导锁：collector 多副本时只有一个调度器投递，其余副本只跑采集。

    锁 TTL 略大于两个轮询周期；持有者每轮续期，崩溃后锁自动过期被其他副本抢占。
    """
    import secrets

    instance = getattr(redis, "_anisignal_sched_id", None)
    if instance is None:
        instance = secrets.token_hex(8)
        redis._anisignal_sched_id = instance  # noqa: SLF001
    got = await redis.set(_LEADER_KEY, instance, nx=True, px=_LEADER_TTL_MS)
    if got:
        return True
    if hasattr(redis, "eval"):
        renewed = await redis.eval(
            "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('pexpire',KEYS[1],ARGV[2]) else return 0 end",
            1, _LEADER_KEY, instance, _LEADER_TTL_MS,
        )
        return bool(renewed)
    current = await redis.get(_LEADER_KEY)
    if isinstance(current, bytes):
        current = current.decode()
    if current == instance:
        await redis.pexpire(_LEADER_KEY, _LEADER_TTL_MS)
        return True
    return False


async def _enqueue_morning_if_due(redis, now: datetime) -> None:
    due, day = _morning_due(now)
    if not due:
        return
    key = f"anisignal:morning-enqueued:{day}"
    if await redis.get(key):
        return
    job = await redis.enqueue_job(
        "send_morning_notifications",
        _job_id=f"morning:{day}",
    )
    await redis.set(key, "1", nx=True, px=36 * 60 * 60 * 1000)
    logger.info("enqueued morning notifications for %s job=%s", day, getattr(job, "job_id", None))


async def _enqueue_reports_if_due(redis, now: datetime) -> None:
    """北京时间 08:30 投递通用周期报告批次（走慢 AI 队列）。"""
    due, day = _reports_due(now)
    if not due:
        return
    key = f"anisignal:periodic-reports-enqueued:{day}"
    if await redis.get(key):
        return
    from app.services.queue import AI_QUEUE_NAME

    job = await redis.enqueue_job(
        "send_periodic_reports",
        _job_id=f"periodic-reports:{day}",
        _queue_name=AI_QUEUE_NAME,
    )
    await redis.set(key, "1", nx=True, px=36 * 60 * 60 * 1000)
    logger.info("enqueued periodic reports for %s job=%s", day, getattr(job, "job_id", None))


async def _enqueue_retention_if_due(redis, now: datetime) -> None:
    """北京时间凌晨 3～6 点跑一次数据保留，错过当天的窗口就等下一天。"""
    local = now.replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    if not 3 <= local.hour < 6:
        return
    day = local.date().isoformat()
    key = f"anisignal:retention-enqueued:{day}"
    if await redis.get(key):
        return
    await redis.enqueue_job("retention_sweep", _job_id=f"retention:{day}")
    await redis.set(key, "1", nx=True, px=36 * 60 * 60 * 1000)
    logger.info("enqueued retention sweep for %s", day)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    redis = await get_redis_pool()
    logger.info("scheduler started, poll=%.0fs", settings.scheduler_poll_seconds)
    try:
        while True:
            try:
                if not await _is_leader(redis):
                    logger.info("not scheduler leader this tick, standby")
                    await asyncio.sleep(settings.scheduler_poll_seconds)
                    continue
                now = utcnow()
                async with SessionLocal() as session:
                    due = await load_due_targets(session, now)
                    for target in due:
                        target.next_run_at = next_run_with_jitter(target, now)
                        await session.commit()
                        job = await redis.enqueue_job(
                            "collect_target", target.id, _job_id=f"collect:{target.id}:{now.timestamp()}")
                        logger.info(
                            "enqueued target %s (%s/%s) job=%s next_run_at=%s",
                            target.id,
                            target.platform,
                            target.target_value,
                            getattr(job, "job_id", None),
                            target.next_run_at,
                        )
                    await _enqueue_morning_if_due(redis, now)
                    await _enqueue_reports_if_due(redis, now)
                    await _enqueue_retention_if_due(redis, now)
                    # 半小时检测一次授权状态；固定桶 ID 在任务结果保留期内去重。
                    await redis.enqueue_job("check_bilibili_session", _job_id=f"bili-session:{int(now.timestamp() // 1800)}")
                    await redis.enqueue_job("send_bot_notifications", _job_id=f"bot-sweep:{int(now.timestamp() // 60)}")
                    await redis.enqueue_job("send_instant_notifications", _job_id="instant-notifications")
                    # 定制监控与慢 AI 轮询：5 分钟桶去重，AI 任务全部走慢队列避免堵死采集与实时推送
                    from app.services.queue import AI_QUEUE_NAME

                    bucket = int(now.timestamp() // 300)
                    if not await redis.get(f"anisignal:news-editorial-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "news_editorial_sweep", _job_id="news-editorial", _queue_name=AI_QUEUE_NAME
                        )
                        if job is not None:
                            await redis.set(f"anisignal:news-editorial-enqueued:{bucket}", "1", nx=True, px=6 * 60 * 60 * 1000)
                    await redis.enqueue_job(
                        "review_pending_news", _job_id=f"review-pending-news:{bucket}", _queue_name=AI_QUEUE_NAME)
                    await redis.enqueue_job(
                        "brief_pending_contents", _job_id=f"brief-pending:{bucket}", _queue_name=AI_QUEUE_NAME)
                    await redis.enqueue_job(
                        "review_pending_fulfillment", _job_id=f"review-pending-fulfillment:{bucket}", _queue_name=AI_QUEUE_NAME)
                    if not await redis.get(f"anisignal:custom-sweep-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "collect_custom_feeds", _job_id=f"custom-sweep:{bucket}")
                        await redis.set(
                            f"anisignal:custom-sweep-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued custom sweep job=%s", getattr(job, "job_id", None))
                    # 内容实体抽取：同 5 分钟桶去重（日程/兑换码，走慢 AI 队列）
                    if not await redis.get(f"anisignal:extract-sweep-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "extract_entities_sweep", _job_id=f"extract-sweep:{bucket}", _queue_name=AI_QUEUE_NAME)
                        await redis.set(
                            f"anisignal:extract-sweep-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued extract sweep job=%s", getattr(job, "job_id", None))
                    if not await redis.get(f"anisignal:product-monitor-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "product_monitor_sweep", _job_id=f"product-monitor:{bucket}", _queue_name=AI_QUEUE_NAME)
                        await redis.set(
                            f"anisignal:product-monitor-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued product monitor job=%s", getattr(job, "job_id", None))
            except Exception:  # noqa: BLE001 - 调度循环不能因单轮失败退出
                logger.exception("scheduler tick failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
