"""直发和插件领取共用的发送门禁；预算按已开始发送的消息计算。"""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import BotClient, BotDelivery, BotTarget
from app.services.account_limits import QUOTA_EXHAUSTED, claim_notification

AUTOMATIC_KINDS = {"daily", "updates", "calendar", "critical"}
_target_locks = defaultdict(asyncio.Lock)


def is_quiet(now: datetime, config: dict) -> bool:
    hour = (now + timedelta(hours=8)).hour
    start, end = config.get("quiet_start", 23), config.get("quiet_end", 8)
    return start <= hour < end if start < end else (hour >= start or hour < end) if start != end else False


async def send_budget_exhausted(session, target_id: int, config: dict, now: datetime) -> bool:
    """按实际开始发送的时间判断每小时额度与最小间隔。

    不加 FOR UPDATE：领取路径已持有目标行锁，而 bot_deliveries 只有 target_id 索引，
    范围锁会锁住该会话全部历史投递，与回执更新、新消息入队互相死锁。
    """
    limit = int(config.get("hourly_limit", 2) or 0)
    gap = int(config.get("min_push_interval_minutes", 20) or 0)
    recent = list(await session.scalars(select(BotDelivery.updated_at).where(
        BotDelivery.target_id == target_id, BotDelivery.kind.in_(AUTOMATIC_KINDS),
        BotDelivery.status.in_(["sending", "accepted", "unknown"]),
        BotDelivery.updated_at >= now - timedelta(minutes=max(60, gap)))))
    if limit and sum(stamp >= now - timedelta(hours=1) for stamp in recent) >= limit:
        return True
    return bool(gap and recent and max(recent) > now - timedelta(minutes=gap))


async def delivery_congested(session, target, now: datetime) -> bool:
    """已有消息待发或额度用尽时，新动态应留在缓冲里，等下一个名额合并发出。"""
    waiting = await session.scalar(select(BotDelivery.id).where(
        BotDelivery.target_id == target.id, BotDelivery.kind.in_(AUTOMATIC_KINDS),
        BotDelivery.status.in_(["pending", "failed"]), BotDelivery.attempts < 3,
        # 超过发送窗口的旧消息领取时会被取消，不能让它一直占着队列。
        BotDelivery.created_at > now - timedelta(hours=12)).limit(1))
    return waiting is not None or await send_budget_exhausted(session, target.id, target.config, now)


async def reserve_delivery(session, target, row, *, now=None, receipt=None, force=False) -> bool:
    """锁住目标到预算检查和 sending 提交完成，避免并发领取一起穿过上限。"""
    from app.services.bot import prepare_delivery

    now = now or utcnow()
    async with _target_locks[target.id]:
        # 先结束调用方事务：下面的预算计数是普通读，必须在拿到目标行锁之后才建立快照。
        await session.commit()
        # MySQL 行锁跨进程生效；进程锁也让 SQLite 本地模式保持相同语义。
        target = await session.scalar(select(BotTarget).where(BotTarget.id == target.id).with_for_update().execution_options(populate_existing=True))
        row = await session.scalar(select(BotDelivery).where(BotDelivery.id == row.id).with_for_update().execution_options(populate_existing=True))
        if not row or row.status not in {"pending", "failed"} or row.attempts >= 3:
            await session.commit()
            return False
        if not target.enabled and row.kind != "manual":
            await session.commit()
            return False
        if row.status == "failed" and row.updated_at > now - timedelta(minutes=5) and not force:
            await session.commit()
            return False
        if not await prepare_delivery(session, row, target=target, now=now, commit=False):
            await session.commit()
            return False
        config = target.config
        if row.kind == "reminder" and not config.get("reminders_enabled"):
            await session.commit()
            return False
        if row.kind in AUTOMATIC_KINDS:
            if is_quiet(now, config) and (row.kind != "critical" or not config.get("quiet_critical", False)):
                await session.commit()
                return False
            if await send_budget_exhausted(session, target.id, config, now):
                await session.commit()
                return False
        client = await session.get(BotClient, target.client_id)
        if client and not await claim_notification(session, client.user_id, now=now):
            row.status, row.receipt, row.error, row.updated_at = "cancelled", None, QUOTA_EXHAUSTED, now
            await session.commit()
            return False
        row.status, row.receipt = "sending", receipt
        row.attempts += 1
        row.updated_at = now
        await session.commit()
        return True
