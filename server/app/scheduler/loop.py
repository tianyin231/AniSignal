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
_CUSTOM_SWEEP_INTERVAL = timedelta(minutes=5)


def _morning_due(now: datetime) -> tuple[bool, str]:
    local = now.replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    return local.hour >= 8, local.date().isoformat()


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
                if due_now and _local_morning_date != day:
                    await queue.enqueue_morning()
                    _local_morning_date = day
                    logger.info("local enqueued morning notifications for %s", day)
                global _local_custom_sweep_at
                if _local_custom_sweep_at is None or now >= _local_custom_sweep_at:
                    await queue.enqueue_custom_sweep()
                    _local_custom_sweep_at = now + _CUSTOM_SWEEP_INTERVAL
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
                    # 定制监控轮询：5 分钟桶去重，worker 内部再按 next_run_at 限速
                    bucket = int(now.timestamp() // 300)
                    if not await redis.get(f"anisignal:custom-sweep-enqueued:{bucket}"):
                        job = await redis.enqueue_job(
                            "collect_custom_feeds", _job_id=f"custom-sweep:{bucket}")
                        await redis.set(
                            f"anisignal:custom-sweep-enqueued:{bucket}", "1",
                            nx=True, px=6 * 60 * 60 * 1000)
                        logger.info("enqueued custom sweep job=%s", getattr(job, "job_id", None))
            except Exception:  # noqa: BLE001 - 调度循环不能因单轮失败退出
                logger.exception("scheduler tick failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
