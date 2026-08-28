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


async def summarize_event(ctx, event_id: int) -> dict:
    """AI 事件摘要（未配置 LLM 时安全跳过）。"""
    from app.db import SessionLocal
    from app.services.ai import summarize_event as run

    async with SessionLocal() as session:
        return await run(session, event_id)


async def translate_content(ctx, content_id: int) -> dict:
    """AI 内容机翻（外语平台 → 简体中文）。"""
    from app.db import SessionLocal
    from app.services.ai import translate_content as run

    async with SessionLocal() as session:
        return await run(session, content_id)


async def send_user_notification(ctx, delivery_id: int) -> dict:
    """生成并发送一封用户主动请求的关键词邮件。"""
    from app.db import SessionLocal
    from app.services.notifications import process_delivery

    async with SessionLocal() as session:
        return await process_delivery(session, delivery_id)


async def send_morning_notifications(ctx) -> dict:
    """北京时间 08:00 的用户关键词早报批次。"""
    from app.db import SessionLocal
    from app.services.notifications import run_scheduled_morning

    async with SessionLocal() as session:
        result = await run_scheduled_morning(session)
        logger.info("morning notifications -> %s", result)
        return result


async def on_startup(ctx) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class WorkerSettings:
    functions = [
        collect_target,
        summarize_event,
        translate_content,
        send_user_notification,
        send_morning_notifications,
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 1800
    keep_result = 3600
    on_startup = on_startup
