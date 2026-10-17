"""账号中心商品跟踪（批次 3b）：手动标记状态 + 到价提醒。

用户可标记「已预订 / 已到货」，也可设心理价位；采集到低于该价的观测时，
按既有通知渠道提醒。这里只做用户自己那几行数据，不触碰公共采集范围。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db import get_session
from app.models import Product, TRACK_STATE_LABELS, TRACK_STATES, User
from app.services import product_dossier

router = APIRouter(prefix="/api/account/product-tracker", dependencies=[Depends(get_current_user)])

# 静态路径（alerts/history）必须早于 /{product_id}/... 注册，否则会被路径参数吞掉
_STATE_OPTIONS = [
    {"value": state, "label": TRACK_STATE_LABELS[state]}
    for state in TRACK_STATES
]


async def _product_or_404(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在")
    return product


@router.get("/alerts/history")
async def alert_history(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
):
    """我的到价提醒触发记录（含当前是否仍低于心理价位）。"""
    from app.models import ProductPriceAlert, ProductProfile

    rows = (
        await session.execute(
            select(ProductPriceAlert, Product, ProductProfile)
            .join(Product, Product.id == ProductPriceAlert.product_id)
            .join(ProductProfile, ProductProfile.product_id == Product.id, isouter=True)
            .where(
                ProductPriceAlert.user_id == user.id,
                ProductPriceAlert.trigger_count > 0,
            )
            .order_by(ProductPriceAlert.triggered_at.desc().nullslast(), ProductPriceAlert.id.desc())
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": alert.id,
                "product_id": product.id,
                "slug": product.slug,
                "name": product.name,
                "target_price": alert.target_price,
                "triggered_at": alert.triggered_at,
                "triggered_price": alert.triggered_price,
                "trigger_count": alert.trigger_count,
                "price_current": profile.price_current if profile else None,
                "still_below": bool(
                    profile is not None
                    and profile.price_current is not None
                    and profile.price_current < alert.target_price
                ),
            }
            for alert, product, profile in rows
        ]
    }


@router.get("")
async def list_tracker(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """我的商品跟踪列表（手动状态 + 到价提醒 + 当前观测价）。"""
    return {"items": await product_dossier.list_tracker(session, user.id), "state_options": _STATE_OPTIONS}


@router.put("/{product_id}/state")
async def set_state(
    product_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """标记商品状态：watching / ordered / arrived / dropped。"""
    await _product_or_404(session, product_id)
    state = str(body.get("state") or "").strip()
    if state not in TRACK_STATES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"状态只支持 {' / '.join(TRACK_STATES)}",
        )
    note = str(body.get("note") or "").strip()[:255] or None
    row = await product_dossier.set_track_state(session, user.id, product_id, state, note)
    return {
        "product_id": product_id,
        "state": row.state,
        "state_label": product_dossier._track_label(row.state),
        "note": row.note,
        "marked_at": row.marked_at,
    }


@router.delete("/{product_id}/state", status_code=status.HTTP_204_NO_CONTENT)
async def clear_state(
    product_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _product_or_404(session, product_id)
    await product_dossier.clear_track_state(session, user.id, product_id)


@router.post("/{product_id}/price-alert", status_code=status.HTTP_201_CREATED)
async def set_price_alert(
    product_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """设心理价位：采集到低于该价的观测即按既有渠道提醒。"""
    await _product_or_404(session, product_id)
    try:
        row = await product_dossier.set_price_alert(
            session, user.id, product_id, body.get("target_price")
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {
        "id": row.id,
        "product_id": product_id,
        "target_price": row.target_price,
        "enabled": row.enabled,
        "triggered_at": row.triggered_at,
        "triggered_price": row.triggered_price,
        "trigger_count": row.trigger_count,
    }


@router.patch("/{product_id}/price-alert")
async def toggle_price_alert(
    product_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """启停到价提醒（不删除设置，方便临时静音）。"""
    await _product_or_404(session, product_id)
    from app.models import ProductPriceAlert

    row = await session.scalar(
        select(ProductPriceAlert).where(
            ProductPriceAlert.user_id == user.id, ProductPriceAlert.product_id == product_id
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "还没有设置到价提醒")
    row.enabled = bool(body.get("enabled", True))
    if row.enabled:
        # 重新启用视为新一轮提醒
        row.triggered_at = None
        row.triggered_price = None
    await session.commit()
    await session.refresh(row)
    return {
        "id": row.id,
        "product_id": product_id,
        "target_price": row.target_price,
        "enabled": row.enabled,
        "triggered_at": row.triggered_at,
        "triggered_price": row.triggered_price,
        "trigger_count": row.trigger_count,
    }


@router.delete("/{product_id}/price-alert", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price_alert(
    product_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _product_or_404(session, product_id)
    await product_dossier.clear_price_alert(session, user.id, product_id)
