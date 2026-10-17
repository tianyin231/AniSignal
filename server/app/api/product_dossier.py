"""商品档案公开接口（批次 3b）：档案头、价格/热度曲线、AI 差评聚合与发售日历。

与 `/api/products/{slug}` 的分工：后者回答「履约到哪一步了」，这里回答
「这一档商品的完整档案」——封面、官方链接、公告时间线、价格曲线、质量反馈。
全部只读，不需要登录。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import product_dossier

router = APIRouter(tags=["product-dossier"])

# 发售日历单独占一个路径：/api/calendar 已是「活动日程」日历，
# 而 /api/products/{slug} 会吞掉同前缀的静态路径，所以不复用 products 前缀。


async def _product_id(session: AsyncSession, slug: str) -> int:
    from app.models import Product

    product_id = await session.scalar(select(Product.id).where(Product.slug == slug))
    if product_id is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return product_id


@router.get("/api/release-calendar")
async def release_calendar(session: AsyncSession = Depends(get_session)):
    """发售日历：即将发售 / 预售与已发售未发货 / 已结束。"""
    return await product_dossier.calendar(session)


@router.get("/api/products/{slug}/dossier")
async def get_dossier(
    slug: str,
    session: AsyncSession = Depends(get_session),
    days: int = Query(default=90, ge=7, le=365),
):
    """一物一档：档案头 + 官方公告时间线 + 价格/热度曲线 + AI 质量反馈。"""
    product_id = await _product_id(session, slug)
    payload = await product_dossier.dossier(session, product_id, series_days=days)
    if not payload:
        raise HTTPException(status_code=404, detail="商品不存在")
    return payload


@router.get("/api/products/{slug}/series")
async def get_series(
    slug: str,
    session: AsyncSession = Depends(get_session),
    days: int = Query(default=90, ge=7, le=365),
):
    """价格/热度曲线数据（按天聚合，前端自行画图）。"""
    product_id = await _product_id(session, slug)
    return {"items": await product_dossier.series(session, product_id, days)}


@router.get("/api/products/{slug}/feedback")
async def get_feedback(
    slug: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
):
    """AI 质量反馈聚合（差评为重点，保留原文链接便于核对）。"""
    product_id = await _product_id(session, slug)
    return {"items": await product_dossier.list_feedback(session, product_id, limit)}
