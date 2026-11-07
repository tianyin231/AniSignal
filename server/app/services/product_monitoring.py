"""商品专属监控的关联与维护。

周期按交付进度结束，不看有没有新内容：预售可能持续半年以上，这期间本来就没有用户内容，
安静不代表商品结束。搜索频率同样跟着阶段走，等待发货时不必高频搜索。
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import CollectTarget, Product

DELIVERY_GRACE = timedelta(days=14)
SHIP_WINDOW = timedelta(days=30)
LATE_SHIP_WINDOW = timedelta(days=60)
RECENT_DELIVERY_SIGNAL = timedelta(days=14)
WATCH_INTERVAL_MINUTES = 30
WAITING_INTERVAL_MINUTES = 360
# 其他间隔是管理员手动设的，自动调频不覆盖。
_AUTO_INTERVALS = {WATCH_INTERVAL_MINUTES, WAITING_INTERVAL_MINUTES}
_ROUND_SIGNALS = {"presale_open", "balance_open", "delayed"}
_DELIVERY_SIGNALS = {"shipping_started", "consumer_received"}


def delivery_confirmed_at(
    rows: list[tuple], expected_ship_at: datetime | None, now: datetime,
) -> datetime | None:
    """本轮交付的确认时间，rows 为 (信号, 证据)。

    单条用户开箱不足以结束周期：线上出现过盗版、预定卡和同名旧款被认成到货。
    所以要官方发货、两位不同作者开箱，或预计出货期内有人开箱。官方开了新一轮预售、
    尾款或延期，就只看这之后的证据，否则旧批次会把新一轮提前结束。
    """
    if expected_ship_at and expected_ship_at > now + SHIP_WINDOW:
        return None
    verified = [(signal, evidence) for signal, evidence in rows
                if signal.review_status == "verified" and signal.occurred_at]
    round_at = max((signal.occurred_at for signal, _ in verified
                    if signal.actor_type == "official" and signal.signal_type in _ROUND_SIGNALS), default=None)
    current = sorted(((signal, evidence) for signal, evidence in verified
                      if round_at is None or signal.occurred_at >= round_at),
                     key=lambda row: row[0].occurred_at)
    confirmed = [signal.occurred_at for signal, _ in current
                 if signal.signal_type == "shipping_started" and signal.actor_type == "official"]
    reporters: dict[str, datetime] = {}
    for signal, evidence in current:
        if (signal.signal_type == "consumer_received" and signal.actor_type != "seeded"
                and evidence.acquisition_type != "seeded"):
            reporters.setdefault(evidence.independent_source_key or f"signal:{signal.id}", signal.occurred_at)
    reports = sorted(reporters.values())
    if len(reports) >= 2:
        confirmed.append(reports[1])
    if expected_ship_at:
        confirmed.extend(reported for reported in reports if reported >= expected_ship_at - SHIP_WINDOW)
    return min(confirmed, default=None)


def monitor_interval_minutes(
    rows: list[tuple], expected_ship_at: datetime | None, confirmed_at: datetime | None, now: datetime,
) -> int:
    """发货窗口内或刚出现到货迹象时高频搜索，其余时间等待。"""
    if confirmed_at:
        return WATCH_INTERVAL_MINUTES
    if expected_ship_at and expected_ship_at - SHIP_WINDOW <= now <= expected_ship_at + LATE_SHIP_WINDOW:
        return WATCH_INTERVAL_MINUTES
    if any(signal.signal_type in _DELIVERY_SIGNALS and signal.review_status in {"candidate", "verified"}
           and signal.occurred_at and signal.occurred_at >= now - RECENT_DELIVERY_SIGNAL for signal, _ in rows):
        return WATCH_INTERVAL_MINUTES
    return WAITING_INTERVAL_MINUTES


async def apply_monitor_interval(
    session: AsyncSession, product: Product, minutes: int, now: datetime,
) -> bool:
    if not product.monitor_target_id:
        return False
    target = await session.get(CollectTarget, product.monitor_target_id)
    if (target is None or not target.enabled or target.interval_minutes == minutes
            or target.interval_minutes not in _AUTO_INTERVALS):
        return False
    target.interval_minutes = minutes
    # 从等待转入发货窗口时不能再等完旧周期。
    soonest = now + timedelta(minutes=minutes)
    if target.next_run_at is None or target.next_run_at > soonest:
        target.next_run_at = soonest
    return True


async def configure_product_monitor(
    session: AsyncSession, product: Product, *, enabled: bool | None = None,
    keyword: str | None = None, interval_minutes: int | None = None,
    min_views: int | None = None,
) -> None:
    current = await session.get(CollectTarget, product.monitor_target_id) if product.monitor_target_id else None
    if current is None and enabled is not True and keyword is None:
        return
    keyword = (keyword or (current.target_value if current else product.name)).strip()
    if not keyword:
        raise ValueError("商品监控搜索词不能为空")
    target = await session.scalar(select(CollectTarget).where(
        CollectTarget.platform == "bilibili",
        CollectTarget.target_type == "keyword_search",
        CollectTarget.target_value == keyword,
    ).with_for_update())
    if target is not None:
        if target.community != product.community and target is not current:
            raise ValueError("该搜索词已用于其他分区，请设置包含游戏名称的独立搜索词")
        owner = await session.scalar(select(Product.id).where(
            Product.monitor_target_id == target.id, Product.id != product.id,
        ))
        if owner is not None:
            raise ValueError("该搜索词已由其他商品使用，请设置独立搜索词")
    else:
        target = CollectTarget(
            platform="bilibili", target_type="keyword_search", target_value=keyword,
            community=product.community, co_terms=[], tier="core", min_views=0,
            interval_minutes=current.interval_minutes if current else 30,
            enabled=current.enabled if current else True,
        )
        session.add(target)
        await session.flush()
    if current is not None and current is not target:
        target.enabled = current.enabled
        target.interval_minutes = current.interval_minutes
        target.min_views = current.min_views
        current.enabled = False
    target.community = product.community
    target.display_name = f"B站搜索·{product.name}"
    target.tier = "core"
    if enabled is not None:
        target.enabled = enabled
        if enabled and product.monitor_status == "archived":
            product.monitor_status = "watching"
            product.archived_at = None
            product.monitor_started_at = utcnow()
    if interval_minutes is not None:
        target.interval_minutes = interval_minutes
    if min_views is not None:
        target.min_views = min_views
    target.next_run_at = utcnow()
    product.monitor_target_id = target.id
