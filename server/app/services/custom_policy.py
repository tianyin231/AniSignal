"""受控公测额度、按独立目标计算容量，以及公共/定制共用的 B站任务预算。"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.clock import utcnow
from app.collectors.base import CollectorDeferred
from app.models import CustomFeed, CustomFeedSource, SiteSetting, User
from app.services.settings import get_setting

DEFAULT_POLICY = {
    "open_beta": False, "allowed_user_ids": [], "user_overrides": {},
    "max_feeds_per_user": 10, "min_interval_minutes": 30, "max_active_sources": 60,
    "custom_hourly_budget": 120, "platform_hourly_budget": 360,
}


async def get_policy(session):
    return {**DEFAULT_POLICY, **(await get_setting(session, "custom_feed_policy") or {})}


async def user_quota(session, user_id, *, lock=False):
    policy = await get_policy(session)
    query = select(CustomFeed.id).where(CustomFeed.user_id == user_id)
    if lock:
        query = query.with_for_update()
    count = len(list(await session.scalars(query)))
    overrides = policy["user_overrides"].get(str(user_id), {})
    user = await session.get(User, user_id)
    return {
        "admitted": bool(user and user.status == "active" and user.is_verified and
                         (policy["open_beta"] or user_id in policy["allowed_user_ids"] or count > 0)),
        "used": count,
        "max_feeds": max(count, overrides.get("max_feeds", policy["max_feeds_per_user"])),
        "min_interval_minutes": overrides.get("min_interval_minutes", policy["min_interval_minutes"]),
    }


async def capacity(session):
    # 共享目标取订阅者要求的最短周期；人数不直接决定平台任务数。
    rows = (await session.execute(select(CustomFeed.platform, CustomFeed.kind, CustomFeed.target_value, CustomFeed.interval_minutes)
        .join(User, User.id == CustomFeed.user_id).where(CustomFeed.enabled.is_(True), CustomFeed.status == "active",
            User.status == "active", User.is_verified.is_(True)))).all()
    targets = {}
    for p, k, t, minutes in rows:
        key = (p, k, canonical_target(k, t))
        targets[key] = min(targets.get(key, minutes), minutes)
    return {"active_sources": len(targets), "estimated_tasks_per_hour": round(sum(60 / max(1, v) for v in targets.values()), 2)}


async def check_admission(session, user_id, platform, kind, target, interval, *, exclude_id=None):
    await insert_ignoring_duplicate(session, SiteSetting, key="custom_admission_lock", value={})
    quota = await user_quota(session, user_id, lock=True)
    if exclude_id is None and not quota["admitted"]:
        raise HTTPException(403, "定制采集正在受控公测，请联系管理员开通名额")
    if exclude_id is None and quota["used"] >= quota["max_feeds"]:
        raise HTTPException(400, f"最多 {quota['max_feeds']} 个定制监控")
    existing = await session.get(CustomFeed, exclude_id) if exclude_id else None
    # 老用户快于新下限的周期可保留；仅新增或主动加速时执行新配额。
    minimum = min(quota["min_interval_minutes"], existing.interval_minutes) if existing else quota["min_interval_minutes"]
    if interval < minimum:
        raise HTTPException(400, f"当前采集周期至少 {minimum} 分钟")
    policy = await get_policy(session)
    feeds = list(await session.scalars(select(CustomFeed).join(User, User.id == CustomFeed.user_id)
        .where(CustomFeed.enabled.is_(True), CustomFeed.status == "active", User.status == "active", User.is_verified.is_(True))
        .with_for_update().execution_options(populate_existing=True)))
    def estimate(values):
        targets = {}
        for p, k, t, minutes in values:
            key = (p, k, canonical_target(k, t))
            targets[key] = min(targets.get(key, minutes), minutes)
        return len(targets), sum(60 / max(1, v) for v in targets.values())
    before = [(f.platform, f.kind, f.target_value, f.interval_minutes) for f in feeds]
    after = [(f.platform, f.kind, f.target_value, f.interval_minutes) for f in feeds if f.id != exclude_id]
    after.append((platform, kind, target, interval))
    old_n, old_cost = estimate(before); new_n, new_cost = estimate(after)
    if (new_n > max(old_n, policy["max_active_sources"]) or new_cost > max(old_cost, policy["custom_hourly_budget"]) + 1e-6):
        raise HTTPException(429, "定制采集容量已满，请延长周期或等待管理员扩容")


def canonical_target(kind, target):
    value = target.strip()
    return str(int(value)) if kind == "uid" else value.casefold()


async def insert_ignoring_duplicate(session, model, **values):
    if session.get_bind().dialect.name == "mysql":
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(model).values(**values)
        key = next(iter(values))
        await session.execute(stmt.on_duplicate_key_update(**{key: getattr(stmt.inserted, key)}))
    else:
        from sqlalchemy.dialects.sqlite import insert
        await session.execute(insert(model).values(**values).on_conflict_do_nothing())


async def shared_source(session, platform, kind, target):
    target = canonical_target(kind, target)
    await insert_ignoring_duplicate(session, CustomFeedSource, platform=platform, kind=kind, target_value=target)
    return await session.scalar(select(CustomFeedSource).where(CustomFeedSource.platform == platform,
        CustomFeedSource.kind == kind, CustomFeedSource.target_value == target))


def request_interval(source, interval):
    if source.last_success_at and source.next_run_at and not source.last_error:
        source.next_run_at = min(source.next_run_at, source.last_success_at + timedelta(minutes=interval))


async def claim_collection_budget(session, platform, *, custom=False):
    """计量单位为一次目标采集任务，包含失败尝试，不把详情 HTTP 次数说成任务数。"""
    if platform != "bilibili":
        return
    policy = await get_policy(session)
    now = utcnow(); hour = now.replace(minute=0, second=0, microsecond=0)
    key = "collector_budget_bilibili"
    await insert_ignoring_duplicate(session, SiteSetting, key=key, value={})
    row = await session.scalar(select(SiteSetting).where(SiteSetting.key == key).with_for_update().execution_options(populate_existing=True))
    value = dict(row.value) if row.value.get("hour") == hour.isoformat() else {"hour": hour.isoformat(), "total": 0, "custom": 0}
    if value["total"] >= policy["platform_hourly_budget"] or (custom and value["custom"] >= policy["custom_hourly_budget"]):
        await session.commit()
        raise CollectorDeferred("B站采集任务预算已用完，下个小时恢复", retry_at=hour + timedelta(hours=1))
    value["total"] += 1; value["custom"] += int(custom)
    row.value = value
    await session.commit()  # 原子占用预算后立即释放行锁，不跨网络持锁。
