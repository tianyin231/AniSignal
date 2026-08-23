"""arq 任务定义：常驻 worker 消费 Redis 队列。

arq CLI 入口：`arq app.worker.settings.WorkerSettings`
"""

import logging

from arq.connections import RedisSettings

from app.config import get_settings
from app.services.collect import run_collection

logger = logging.getLogger(__name__)


async def collect_target(ctx, target_id: int) -> dict:
    """采集一个目标并入库。ctx["redis"] 是 arq 注入的 Redis 池。"""
    result = await run_collection(ctx["redis"], target_id)
    logger.info("collect_target %s -> %s", target_id, result)
    return result


async def ai_placeholder(ctx, raw_content_id: int) -> dict:
    """AI 翻译/摘要/分类占位：本期不实现，接口先立住（设计约束：AI 异步、可重算、独立于采集链路）。"""
    return {"status": "not_implemented", "raw_content_id": raw_content_id}


async def on_startup(ctx) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class WorkerSettings:
    functions = [collect_target, ai_placeholder]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 600
    keep_result = 3600
    on_startup = on_startup
