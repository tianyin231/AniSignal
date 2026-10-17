"""管理端商品档案 API（批次 3b）：档案头维护、价格采样、AI 差评聚合与到价提醒台账。

路由前缀 `/api/admin/product-dossiers`，鉴权走单管理员 Token。
静态路径先注册，`/{product_id}` 放最后，避免被路径参数吞掉。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
from app.models import Product, ProductFeedback
from app.services import product_dossier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/product-dossiers", dependencies=[Depends(require_admin)])


@router.get("/alerts")
async def list_price_alerts(
    session: AsyncSession = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """到价提醒台账：谁给哪个商品设了多少价、触发过几次。"""
    return await product_dossier.list_alerts(session, page=page, page_size=page_size)


@router.post("/alerts/sweep")
async def sweep_price_alerts(session: AsyncSession = Depends(get_session)):
    """手动跑一次到价提醒扫描（线上由调度任务定期跑）。"""
    return await product_dossier.sweep_price_alerts(session)


@router.post("/prices/sweep")
async def sweep_prices(session: AsyncSession = Depends(get_session)):
    """手动跑一次正文价格抽取采样（补曲线用）。"""
    return await product_dossier.sweep_price_points(session)


@router.post("/heat/refresh")
async def refresh_heat(session: AsyncSession = Depends(get_session)):
    """重算所有商品的热度分。"""
    return await product_dossier.refresh_heat(session)


@router.get("")
async def list_dossiers(
    session: AsyncSession = Depends(get_session),
    q: str = Query(default="", max_length=120),
):
    """商品档案列表：档案头 + 当前价 + 曲线点数 + 反馈统计。"""
    from app.models import ProductProfile, ProductPricePoint

    query = select(Product)
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.where(Product.name.like(like) | Product.slug.like(like))
    products = list((await session.execute(query.order_by(Product.id))).scalars())
    product_ids = [product.id for product in products]
    profiles = {
        profile.product_id: profile
        for profile in (
            await session.execute(
                select(ProductProfile).where(ProductProfile.product_id.in_(product_ids))
            )
        ).scalars()
    } if product_ids else {}
    point_counts = {
        int(product_id): int(count)
        for product_id, count in (
            await session.execute(
                select(ProductPricePoint.product_id, func.count(ProductPricePoint.id))
                .where(ProductPricePoint.product_id.in_(product_ids))
                .group_by(ProductPricePoint.product_id)
            )
        ).all()
    } if product_ids else {}
    feedback_counts = {
        int(product_id): int(count)
        for product_id, count in (
            await session.execute(
                select(ProductFeedback.product_id, func.count(ProductFeedback.id))
                .where(ProductFeedback.product_id.in_(product_ids))
                .group_by(ProductFeedback.product_id)
            )
        ).all()
    } if product_ids else {}
    return {
        "items": [
            {
                "product_id": product.id,
                "slug": product.slug,
                "name": product.name,
                "community": product.community,
                "price_note": product.price_note,
                "has_profile": product.id in profiles,
                "cover_url": profiles[product.id].cover_url if product.id in profiles else None,
                "official_url": profiles[product.id].official_url if product.id in profiles else None,
                "release_at": profiles[product.id].release_at if product.id in profiles else None,
                "release_state": profiles[product.id].release_state if product.id in profiles else "unknown",
                "price_current": profiles[product.id].price_current if product.id in profiles else None,
                "price_observed_at": profiles[product.id].price_observed_at if product.id in profiles else None,
                "heat_score": profiles[product.id].heat_score if product.id in profiles else 0,
                "price_points": point_counts.get(product.id, 0),
                "feedback_count": feedback_counts.get(product.id, 0),
            }
            for product in products
        ],
        "total": len(products),
    }


@router.get("/{product_id}")
async def get_dossier(product_id: int, session: AsyncSession = Depends(get_session)):
    """商品档案详情（管理端预览，与公开接口同一份聚合）。"""
    if await session.get(Product, product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在")
    return await product_dossier.dossier(session, product_id)


@router.put("/{product_id}")
async def upsert_dossier(
    product_id: int, body: dict, session: AsyncSession = Depends(get_session)
):
    """维护档案头：封面、官方链接、发售日历与价格。"""
    if await session.get(Product, product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在")
    allowed = {
        "cover_url",
        "official_url",
        "official_name",
        "aliases",
        "release_at",
        "release_state",
        "release_note",
        "price_current",
        "price_source",
        "note",
    }
    payload = {key: value for key, value in (body or {}).items() if key in allowed}
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可更新的字段")
    profile = await product_dossier.upsert_profile(session, product_id, payload)
    return {
        "product_id": product_id,
        "cover_url": profile.cover_url,
        "official_url": profile.official_url,
        "official_name": profile.official_name,
        "aliases": list(profile.aliases or []),
        "release_at": profile.release_at,
        "release_state": profile.release_state,
        "release_note": profile.release_note,
        "price_current": profile.price_current,
        "price_currency": profile.price_currency,
        "price_source": profile.price_source,
        "price_observed_at": profile.price_observed_at,
        "heat_score": profile.heat_score,
        "note": profile.note,
        "updated_at": profile.updated_at,
    }


@router.post("/{product_id}/prices", status_code=status.HTTP_201_CREATED)
async def record_price(
    product_id: int, body: dict, session: AsyncSession = Depends(get_session)
):
    """手工记录一个价格/热度采样点（外部渠道看到的价格也可录入）。"""
    if await session.get(Product, product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在")
    price = body.get("price")
    try:
        price = None if price in (None, "") else float(price)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "价格必须是数字") from exc
    heat = body.get("heat")
    try:
        heat = None if heat in (None, "") else float(heat)
    except (TypeError, ValueError):
        heat = None
    hits = body.get("hits") or 0
    try:
        hits = int(hits)
    except (TypeError, ValueError):
        hits = 0
    row = await product_dossier.record_price_point(
        session,
        product_id,
        price=price,
        heat=heat,
        hits=hits,
        source="manual",
        note=str(body.get("note") or "")[:255] or None,
    )
    return {
        "id": row.id,
        "product_id": product_id,
        "captured_at": row.captured_at,
        "price": row.price,
        "heat": row.heat,
        "hits": row.hits,
        "source": row.source,
    }


@router.post("/{product_id}/feedback/aggregate")
async def aggregate_feedback(product_id: int, session: AsyncSession = Depends(get_session)):
    """对质量反馈类命中跑一次 AI 摘要聚合（未配置 AI 时回退原文片段）。"""
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在")
    return await product_dossier.aggregate_feedback(session, product)


@router.patch("/feedback/{feedback_id}")
async def review_feedback(
    feedback_id: int, body: dict, session: AsyncSession = Depends(get_session)
):
    """人工确认/撤销一条质量反馈（确认后的结论才会在公开档案里突出）。"""
    row = await product_dossier.set_feedback_reviewed(
        session, feedback_id, bool(body.get("reviewed", True))
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "反馈不存在")
    return {
        "id": row.id,
        "product_id": row.product_id,
        "sentiment": row.sentiment,
        "reviewed": row.reviewed,
        "summary": row.summary,
    }
