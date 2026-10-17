"""调度器：常驻进程，按看板配置定时投递采集任务。

设计要点：
- 每 poll 秒扫一次到期目标（enabled && next_run_at <= now）；
- 投递前先把 next_run_at 推后（含抖动），避免投递失败时被反复扫到；
- 失败退避由 services.collect.next_run_with_jitter 统一计算。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.clock import utcnow
from app.config import get_settings
from app.db import SessionLocal
from app.services.collect import get_redis_pool, load_due_targets, next_run_with_jitter

logger = logging.getLogger(__name__)

_SHANGHAI = timezone(timedelta(hours=8))
_local_morning_date = None
_local_custom_sweep_at = None
_local_volume_sweep_at = None
_local_price_sweep_at = None
_local_brief_date = None
_CUSTOM_SWEEP_INTERVAL = timedelta(minutes=5)
_VOLUME_SWEEP_INTERVAL = timedelta(minutes=5)
_PRICE_SWEEP_INTERVAL = timedelta(minutes=15)
# 简报在早报之后半小时生成：早报先把当日关键词邮件发完，简报再回顾前一天
_BRIEF_LOCAL_HOUR = 8


def _morning_due(now: datetime) -> tuple[bool, str]:
    local = now.replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    return local.hour >= 8, local.date().isoformat()


def _brief_due(now: datetime) -> tuple[bool, str]:
    local = now.replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    return local.hour >= _BRIEF_LOCAL_HOUR, local.date().isoformat()


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
                from app.services.webhooks import run_webhook_dispatch
                await run_bot_notifications(session, now)
                await run_webhook_dispatch(session)
                if due_now and _local_morning_date != day:
                    await queue.enqueue_morning()
                    _local_morning_date = day
                    logger.info("local enqueued morning notifications for %s", day)
                global _local_custom_sweep_at
                if _local_custom_sweep_at is None or now >= _local_custom_sweep_at:
                    await queue.enqueue_custom_sweep()
                    _local_custom_sweep_at = now + _CUSTOM_SWEEP_INTERVAL
                # 声量异常扫描：本地模式直接跑（与 webhook 推送同节奏）
                global _local_volume_sweep_at
                if _local_volume_sweep_at is None or now >= _local_volume_sweep_at:
                    from app.services.volume import sweep_volume

                    _local_volume_sweep_at = now + _VOLUME_SWEEP_INTERVAL
                    result = await sweep_volume(session, now)
                    if result.get("anomalies") or result.get("notified"):
                        logger.info("local volume sweep -> %s", result)
                # 到价提醒扫描：价格观测低于心理价位即推送（比声量扫描稀疏，省额度）
                global _local_price_sweep_at
                if _local_price_sweep_at is None or now >= _local_price_sweep_at:
                    from app.services.product_dossier import sweep_price_alerts

                    _local_price_sweep_at = now + _PRICE_SWEEP_INTERVAL
                    result = await sweep_price_alerts(session, now)
                    if result.get("triggered"):
                        logger.info("local price alert sweep -> %s", result)
                # 每日情报简报：北京时间 08:30 之后跑一次，回顾前一天
                global _local_brief_date
                brief_due, brief_day = _brief_due(now)
                if brief_due and _local_brief_date != brief_day:
                    from app.services.briefing import run_daily_brief

                    _local_brief_date = brief_day
                    result = await run_daily_brief(session, now)
                    logger.info("local daily brief -> %s", result)
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


async def _enqueue_daily_brief_if_due(redis, now: datetime) -> None:
    """每日情报简报：北京时间 08:00 后跑一次（早报之后，回顾前一天）。"""
    due, day = _brief_due(now)
    if not due:
        return
    key = f"anisignal:brief-enqueued:{day}"
    if await redis.get(key):
        return
    job = await redis.enqueue_job("generate_daily_brief", _job_id=f"brief:{day}")
    await redis.set(key, "1", nx=True, px=36 * 60 * 60 * 1000)
    logger.info("enqueued daily brief for %s job=%s", day, getattr(job, "job_id", None))


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
                    await _enqueue_daily_brief_if_due(redis, now)
                    await redis.enqueue_job("send_bot_notifications", _job_id=f"bot-sweep:{int(now.timestamp() // 60)}")
                    # Webhook 命中推送：与机器人推送同节奏，5 分钟桶去重
                    webhook_bucket = int(now.timestamp() // 300)
                    if not await redis.get(f"anisignal:webhook-sweep-enqueued:{webhook_bucket}"):
                        job = await redis.enqueue_job(
                            "dispatch_webhooks", _job_id=f"webhook-sweep:{webhook_bucket}")
                        await redis.set(
                            f"anisignal:webhook-sweep-enqueued:{webhook_bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued webhook sweep job=%s", getattr(job, "job_id", None))
                    # 定制监控轮询：5 分钟桶去重，worker 内部再按 next_run_at 限速
                    bucket = int(now.timestamp() // 300)
                    if not await redis.get(f"anisignal:custom-sweep-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "collect_custom_feeds", _job_id=f"custom-sweep:{bucket}")
                        await redis.set(
                            f"anisignal:custom-sweep-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued custom sweep job=%s", getattr(job, "job_id", None))
                    # 官方内容实体抽取：同 5 分钟桶去重（日程/兑换码）
                    if not await redis.get(f"anisignal:extract-sweep-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "extract_entities_sweep", _job_id=f"extract-sweep:{bucket}")
                        await redis.set(
                            f"anisignal:extract-sweep-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued extract sweep job=%s", getattr(job, "job_id", None))
                    # 声量异常扫描：同 5 分钟桶去重（窗口基线 + 热度预警）
                    if not await redis.get(f"anisignal:volume-sweep-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "sweep_volume_alerts", _job_id=f"volume-sweep:{bucket}")
                        await redis.set(
                            f"anisignal:volume-sweep-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued volume sweep job=%s", getattr(job, "job_id", None))
                    # 到价提醒扫描：同 15 分钟桶去重（价格观测低于心理价位才推送）
                    price_bucket = int(now.timestamp() // 900)
                    if not await redis.get(f"anisignal:price-sweep-enqueued:{price_bucket}"):
                        job = await redis.enqueue_job(
                            "sweep_price_alerts", _job_id=f"price-sweep:{price_bucket}")
                        await redis.set(
                            f"anisignal:price-sweep-enqueued:{price_bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued price alert sweep job=%s", getattr(job, "job_id", None))
            except Exception:  # noqa: BLE001 - 调度循环不能因单轮失败退出
                logger.exception("scheduler tick failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
