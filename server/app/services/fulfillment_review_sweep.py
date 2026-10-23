"""补齐履约审核：版本不完整才付费，失败冷却后继续，人工锁定不触碰。"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import AiUsage, ContentReview, ContentVersion, Product, ProductSignal, ProductSignalEvidence, RawContent
from app.services.ai import ai_available, get_ai_config
from app.services.fulfillment_automation import has_current_fulfillment_review

REVIEW_BATCH_SIZE = 8
REVIEW_COOLDOWN_MS = 30 * 60 * 1000


async def enqueue_pending_fulfillment_reviews(
    session: AsyncSession, redis, enqueue_review, *, now: datetime | None = None,
    limit: int = REVIEW_BATCH_SIZE,
) -> dict:
    """enqueue_review 接受内容 ID 列表；生产与本地调度共用同一选择和冷却规则。"""
    if not ai_available(await get_ai_config(session)):
        return {"candidates": 0, "queued": 0, "skipped_reason": "ai_not_configured"}
    now = now or utcnow()
    latest_version = select(func.max(ContentVersion.version)).where(
        ContentVersion.raw_content_id == RawContent.id,
    ).correlate(RawContent).scalar_subquery()
    last_attempt = select(func.max(AiUsage.created_at)).where(
        AiUsage.task == "review_content", AiUsage.ref_id == RawContent.id,
    ).correlate(RawContent).scalar_subquery()
    rows = (await session.execute(select(
        RawContent.id, latest_version.label("version"), ContentReview.fulfillment, Product.id.label("product_id"),
    ).join(ProductSignalEvidence, ProductSignalEvidence.raw_content_id == RawContent.id)
    .join(ProductSignal, ProductSignal.id == ProductSignalEvidence.signal_id)
    .join(Product, Product.id == ProductSignal.product_id)
    .outerjoin(ContentReview, ContentReview.raw_content_id == RawContent.id).where(
        RawContent.hidden.is_(False), latest_version.is_not(None),
        ProductSignalEvidence.role != "excluded",
        ProductSignal.active.is_(True), ProductSignal.locked.is_(False),
        ProductSignal.community == RawContent.community, Product.community == RawContent.community,
        last_attempt.is_(None) | (last_attempt <= now - timedelta(minutes=10)),
    ).order_by(RawContent.id))).all()
    contents = {}
    for row in rows:
        value = contents.setdefault(row.id, {"row": row, "products": set()})
        value["products"].add(row.product_id)
    pending = [value["row"] for value in contents.values() if not has_current_fulfillment_review(
        value["row"].fulfillment, content_version=value["row"].version, product_ids=value["products"],
    )]
    queued = 0
    claimed = 0
    for row in pending:
        # 与现有审核 job 去重互补：任务失败/缺字段也不会每个调度 tick 重新付费。
        if not await redis.set(
            f"anisignal:fulfillment-review:{row.id}:{row.version}", "1", nx=True, px=REVIEW_COOLDOWN_MS,
        ):
            continue
        claimed += 1
        if await enqueue_review([row.id]) is not None:
            queued += 1
        if claimed >= limit:
            break
    return {"candidates": len(pending), "queued": queued, "skipped_reason": None}
