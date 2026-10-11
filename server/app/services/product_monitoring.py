"""商品专属监控的关联与维护。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import CollectTarget, Product


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
    if interval_minutes is not None:
        target.interval_minutes = interval_minutes
    if min_views is not None:
        target.min_views = min_views
    target.next_run_at = utcnow()
    product.monitor_target_id = target.id
