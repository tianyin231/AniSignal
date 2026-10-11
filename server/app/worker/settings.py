"""arq 任务定义：常驻 worker 消费 Redis 队列。

arq CLI 入口：`arq app.worker.settings.WorkerSettings`
"""

import logging

from arq.connections import RedisSettings
from arq.worker import Retry, func

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


async def repair_ai_item(ctx, task: str, ref_id: int, only_missing: bool = True) -> dict:
    from app.db import SessionLocal
    from app.services.ai_maintenance import repair_ai_item as run

    async with SessionLocal() as session:
        return await run(session, task, ref_id, only_missing)


async def send_instant_notifications(ctx) -> dict:
    import secrets
    from app.db import SessionLocal
    from app.services.instant_notifications import run_instant_notifications

    token = secrets.token_hex(16)
    key = "anisignal:instant-notifications-lock"
    if not await ctx["redis"].set(key, token, nx=True, ex=1900):
        return {"status": "busy"}
    try:
        async with SessionLocal() as session:
            return await run_instant_notifications(session)
    finally:
        await ctx["redis"].eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0", 1, key, token)


async def review_contents(ctx, content_ids: list[int]) -> dict:
    """AI 批量筛选与精选评分（失败不影响内容入库）。"""
    from app.db import SessionLocal
    from app.services.curation import review_contents as run

    async with SessionLocal() as session:
        stats = await run(session, content_ids)
        if stats.failed_batches:
            # 交还 worker 槽位，游戏审核和快讯都允许有限的延迟重试。
            raise Retry(defer=600)
        return stats.__dict__


async def send_user_notification(ctx, delivery_id: int) -> dict:
    """生成并发送一封用户主动请求的关键词邮件。"""
    from app.db import SessionLocal
    from app.services.notifications import process_delivery

    async with SessionLocal() as session:
        return await process_delivery(session, delivery_id)


async def review_pending_news(ctx) -> dict:
    """补偿未完成的近期快讯，候选查询负责冷却和次数上限。"""
    from app.db import SessionLocal
    from app.services.ai_news import pending_news_ids
    from app.services.queue import enqueue_review_jobs

    async with SessionLocal() as session:
        ids = await pending_news_ids(session)
    await enqueue_review_jobs(ctx["redis"], ids)
    return {"candidates": len(ids)}


async def collect_custom_feeds(ctx) -> dict:
    """定制监控轮询：抓取到期用户监控源（每轮限量，内部自行限速）。"""
    from app.db import SessionLocal
    from app.services.custom_feeds import sweep_custom_feeds

    async with SessionLocal() as session:
        return await sweep_custom_feeds(session, ctx["redis"])


async def extract_entities_sweep(ctx) -> dict:
    """官方内容实体抽取轮询：日程与兑换码（每轮限量）。"""
    from app.db import SessionLocal
    from app.services.extract import sweep_extractions
    import secrets

    token = secrets.token_hex(16)
    key = "anisignal:extract-sweep-lock"
    if not await ctx["redis"].set(key, token, nx=True, ex=1900):
        return {"status": "busy"}
    try:
        async with SessionLocal() as session:
            result = await sweep_extractions(session)
            if result["candidates"]:
                logger.info("extract_entities_sweep -> %s", result)
            return result
    finally:
        await ctx["redis"].eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0", 1, key, token)


async def send_admin_emails(ctx, delivery_ids: list[int]) -> dict:
    """管理端单发/广播邮件批次：逐条 SMTP，失败不阻断批次。"""
    from app.db import SessionLocal
    from app.services.notifications import send_admin_email

    stats = {"sent": 0, "failed": 0, "skipped": 0}
    async with SessionLocal() as session:
        for delivery_id in delivery_ids:
            result = await send_admin_email(session, delivery_id)
            status = result.get("status", "failed")
            stats[status if status in stats else "failed"] += 1
    return stats


async def send_morning_notifications(ctx) -> dict:
    """北京时间 08:00 的全站早报批次。"""
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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from app.db import SessionLocal
    from app.services.collect import recover_stale_runs

    async with SessionLocal() as session:
        recovered = await recover_stale_runs(session)
    if recovered:
        logger.warning("recovered %s interrupted collection runs", recovered)


async def send_bot_notifications(ctx):
    from app.db import SessionLocal
    from app.services.bot import run_bot_notifications

    # 固定 job ID 不足以阻止不同分钟的任务重叠，批次另持 Redis 锁。
    import secrets
    token = secrets.token_hex(16)
    if not await ctx["redis"].set("anisignal:bot-sweep-lock", token, nx=True, ex=1900):
        return {"status": "busy"}
    try:
        async with SessionLocal() as session:
            return await run_bot_notifications(session)
    finally:
        await ctx["redis"].eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0", 1, "anisignal:bot-sweep-lock", token)


class WorkerSettings:
    functions = [
        send_bot_notifications,
        collect_target,
        summarize_event,
        translate_content,
        func(repair_ai_item, keep_result=0),
        func(send_instant_notifications, keep_result=0),
        func(review_contents, keep_result=0, max_tries=3),
        review_pending_news,
        send_user_notification,
        send_morning_notifications,
        send_admin_emails,
        collect_custom_feeds,
        extract_entities_sweep,
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 1800
    keep_result = 3600
    on_startup = on_startup
