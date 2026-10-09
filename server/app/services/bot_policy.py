"""直发和插件领取共用的发送门禁；预算按已开始发送的消息计算。"""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget

AUTOMATIC_KINDS = {"daily", "updates", "calendar", "critical"}
_target_locks = defaultdict(asyncio.Lock)


def is_quiet(now: datetime, config: dict) -> bool:
    hour = (now + timedelta(hours=8)).hour
    start, end = config.get("quiet_start", 23), config.get("quiet_end", 8)
    return start <= hour < end if start < end else (hour >= start or hour < end) if start != end else False


async def reserve_delivery(session, target, row, *, now=None, receipt=None, force=False) -> bool:
    """锁住目标到预算检查和 sending 提交完成，避免并发领取一起穿过上限。"""
    from app.services.bot import prepare_delivery

    now = now or utcnow()
    async with _target_locks[target.id]:
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
            limit = int(config.get("hourly_limit", 2) or 0)
            gap = int(config.get("min_push_interval_minutes", 20) or 0)
            since = now - timedelta(minutes=max(60, gap))
            recent = list(await session.scalars(select(BotDelivery.updated_at).where(
                BotDelivery.target_id == target.id, BotDelivery.kind.in_(AUTOMATIC_KINDS),
                BotDelivery.status.in_(["sending", "accepted", "unknown"]), BotDelivery.updated_at >= since).with_for_update()))
            if (limit and sum(stamp >= now - timedelta(hours=1) for stamp in recent) >= limit) or (gap and recent and max(recent) > now - timedelta(minutes=gap)):
                await session.commit()
                return False
        row.status, row.receipt = "sending", receipt
        row.attempts += 1
        row.updated_at = now
        await session.commit()
        return True
