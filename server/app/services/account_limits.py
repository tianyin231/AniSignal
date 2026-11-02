"""账号公测额度；邮件与 Bot 在开始发送时共用北京时间每日预算。"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import BotClient, BotTarget, CustomFeed, NotificationDelivery, SiteSetting, User
from app.services.custom_policy import insert_ignoring_duplicate, user_limits

QUOTA_EXHAUSTED = "今日通知额度已用完，明日恢复（北京时间）"


def notification_day(now):
    return (now + timedelta(hours=8)).date().isoformat()


async def quota_view(session, user_id, *, now=None):
    now = now or utcnow()
    row = await session.get(SiteSetting, f"notification_budget_{user_id}")
    used = row.value["used"] if row and row.value.get("day") == notification_day(now) else 0
    clients = select(BotClient.id).where(BotClient.user_id == user_id, BotClient.enabled.is_(True))
    return {
        **await user_limits(session, user_id),
        "custom_feeds_used": await session.scalar(select(func.count()).select_from(CustomFeed).where(
            CustomFeed.user_id == user_id)),
        "bot_clients_used": await session.scalar(select(func.count()).select_from(BotClient).where(
            BotClient.user_id == user_id, BotClient.enabled.is_(True))),
        "bot_targets_used": await session.scalar(select(func.count()).select_from(BotTarget).where(
            BotTarget.client_id.in_(clients), BotTarget.enabled.is_(True))),
        "notifications_used": used,
    }


async def check_bot_targets(session, client_id, enabled_umos):
    # 所有接入共用用户行锁，避免不同 Token 并发启用会话绕过额度。
    user = await session.scalar(select(User).join(BotClient, BotClient.user_id == User.id)
        .where(BotClient.id == client_id).with_for_update())
    if user is None:
        return  # 旧管理员接入没有用户套餐。
    other_clients = select(BotClient.id).where(BotClient.user_id == user.id,
        BotClient.enabled.is_(True), BotClient.id != client_id)
    # 使用锁定读，避免 MySQL 可重复读事务仍看到等待用户锁之前的旧额度。
    others = list(await session.scalars(select(BotTarget.id).where(
        BotTarget.client_id.in_(other_clients), BotTarget.enabled.is_(True)).with_for_update()))
    limit = (await user_limits(session, user.id))["max_bot_targets"]
    if len(others) + len(set(enabled_umos)) > limit:
        raise HTTPException(409, f"当前账号最多启用 {limit} 个 Bot 会话，请停用其他会话后重试")


async def claim_notification(session, user_id, *, now=None):
    """调用方与 sending 状态一同提交；失败尝试也占额度，重试不能无限发送。"""
    now = now or utcnow()
    key = f"notification_budget_{user_id}"
    await insert_ignoring_duplicate(session, SiteSetting, key=key, value={})
    row = await session.scalar(select(SiteSetting).where(SiteSetting.key == key)
        .with_for_update().execution_options(populate_existing=True))
    day = notification_day(now)
    used = row.value["used"] if row.value.get("day") == day else 0
    if used >= (await user_limits(session, user_id))["daily_notifications"]:
        return False
    row.value = {"day": day, "used": used + 1}
    return True


async def reserve_email(session, delivery, *, charge=True):
    row = await session.scalar(select(NotificationDelivery).where(NotificationDelivery.id == delivery.id)
        .with_for_update().execution_options(populate_existing=True))
    if not row or row.status != "pending":
        await session.commit()
        return False
    allowed = not charge or await claim_notification(session, row.user_id)
    row.status = "sending" if allowed else "skipped"
    if not allowed:
        row.error = QUOTA_EXHAUSTED
    await session.commit()
    return allowed
