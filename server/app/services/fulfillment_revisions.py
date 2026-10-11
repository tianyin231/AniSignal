"""只为已采纳履约状态变化记账，待判断候选不构成通知。"""

import json
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductFulfillmentRevision

_EMPTY_STATES = {
    "official": "no_verified_signal",
    "consumer": "no_verified_receipt",
    "quality": "no_verified_issue",
}


def verified_fulfillment_state(snapshot: dict) -> dict:
    result = {}
    for key, empty in _EMPTY_STATES.items():
        axis = snapshot.get(key) or {}
        count = int(axis.get("verified_count") or 0)
        # candidate_attention 是未核验关注态，不能触发已核验状态邮件。
        result[key] = {"state": axis.get("state") if count else empty, "verified_count": count}
    result["sample_count"] = int(snapshot.get("sample_count") or 0)
    result["observation_count"] = int(snapshot.get("observation_count") or 0)
    return result


def _json_snapshot(snapshot: dict) -> dict:
    return json.loads(json.dumps(snapshot, default=lambda value: value.isoformat()
                                 if isinstance(value, (date, datetime)) else str(value)))


async def record_product_fulfillment_change(
    session: AsyncSession, product_id: int, before: dict, after: dict, reason: str,
) -> ProductFulfillmentRevision | None:
    """随业务事务落库；返回新修订，未发生已采纳状态变化时返回 None。"""
    current = verified_fulfillment_state(after)
    if verified_fulfillment_state(before) == current:
        return None
    product = await session.get(Product, product_id, with_for_update=True)
    if product is None:
        return None
    latest = await session.scalar(select(ProductFulfillmentRevision).where(
        ProductFulfillmentRevision.product_id == product_id,
    ).order_by(ProductFulfillmentRevision.id.desc()).limit(1).with_for_update())
    if latest and latest.community == product.community and verified_fulfillment_state(latest.snapshot) == current:
        return None
    revision = ProductFulfillmentRevision(
        product_id=product_id, community=product.community,
        previous_snapshot=_json_snapshot(before), snapshot=_json_snapshot(after), reason=reason,
    )
    session.add(revision)
    await session.flush()
    return revision
