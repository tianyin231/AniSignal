"""调度器：常驻进程，按看板配置定时投递采集任务。

设计要点：
- 每 poll 秒扫一次到期目标（enabled && next_run_at <= now）；
- 投递前先把 next_run_at 推后（含抖动），避免投递失败时被反复扫到；
- 失败退避由 services.collect.next_run_with_jitter 统一计算。
"""

import asyncio
import logging

from app.clock import utcnow
from app.config import get_settings
from app.db import SessionLocal
from app.services.collect import get_redis_pool, load_due_targets, next_run_with_jitter

logger = logging.getLogger(__name__)


async def local_loop(queue) -> None:
    """本地测试模式的调度循环：不走 arq，直接投递到进程内 LocalQueue。"""
    settings = get_settings()
    logger.info("local scheduler started, poll=%.0fs", settings.scheduler_poll_seconds)
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
        except Exception:  # noqa: BLE001 - 调度循环不能因单轮失败退出
            logger.exception("local scheduler tick failed")
        await asyncio.sleep(settings.scheduler_poll_seconds)


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
            except Exception:  # noqa: BLE001 - 调度循环不能因单轮失败退出
                logger.exception("scheduler tick failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
