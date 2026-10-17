"""管理端输出层 API：feed 地址与 token 管理、Webhook 渠道与投递记录。

- feed 列表为每个监控任务/事件自动补 token，直接给出可复制的只读地址；
- token 可重建（换新址，旧址失效）或撤销（链接立即失效，记录保留）；
- webhook 投递记录可按状态/渠道/用户过滤，失败的投递可手动重推。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
from app.models import (
    FEED_KINDS,
    FEED_KIND_EVENT,
    FEED_KIND_TARGET,
    CollectTarget,
    Event,
    User,
    WebhookConfig,
    WebhookDelivery,
)
from app.services.feeds import (
    ensure_feed_tokens,
    feed_url,
    resolve_base_url,
    revoke_feed_token,
    rotate_feed_token,
)
from app.services.webhooks import deliver_webhook

logger = logging.getLogger(__name__)

feeds_router = APIRouter(prefix="/api/admin/feeds", dependencies=[Depends(require_admin)])
webhooks_router = APIRouter(
    prefix="/api/admin/webhooks", dependencies=[Depends(require_admin)]
)


def _kind_or_400(kind: str) -> str:
    if kind not in FEED_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind 只支持 target / event")
    return kind


@feeds_router.get("")
async def list_feeds(
    request: Request,
    kind: str = Query(default="all", pattern="^(all|target|event)$"),
    q: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """全站 feed 地址一览：token 不存在则自动生成（每个任务/事件都有只读 feed）。"""
    base = resolve_base_url(request)
    like = f"%{q.strip()}%" if q.strip() else None
    items: list[dict] = []
    totals: dict[str, int] = {}

    async def _collect(model, feed_kind: str, columns: tuple) -> None:
        conditions = [or_(*[column.like(like) for column in columns])] if like else []
        total = await session.scalar(
            select(func.count(model.id)).where(*conditions)
        ) or 0
        totals[feed_kind] = total
        rows = list(
            (
                await session.execute(
                    select(model)
                    .where(*conditions)
                    .order_by(model.id.desc())
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            ).scalars()
        )
        refs = [(feed_kind, row.id) for row in rows]
        tokens = await ensure_feed_tokens(session, refs)
        for row in rows:
            token = tokens[(feed_kind, row.id)]
            items.append(
                {
                    "kind": feed_kind,
                    "ref_id": row.id,
                    "feed_url": feed_url(base, feed_kind, row.id, token.token),
                    "token": token.token,
                    "token_status": token.status,
                    "generation": token.generation,
                    "last_used_at": token.last_used_at,
                    "revoked_at": token.revoked_at,
                    **_summary(feed_kind, row),
                }
            )

    if kind in ("all", FEED_KIND_TARGET):
        await _collect(
            CollectTarget,
            FEED_KIND_TARGET,
            (CollectTarget.target_value, CollectTarget.display_name, CollectTarget.platform),
        )
    if kind in ("all", FEED_KIND_EVENT):
        await _collect(Event, FEED_KIND_EVENT, (Event.title,))

    return {
        "items": items,
        "total": sum(totals.values()),
        "totals": totals,
        "page": page,
        "page_size": page_size,
    }


def _summary(kind: str, row) -> dict:
    if kind == FEED_KIND_TARGET:
        return {
            "title": row.display_name or row.target_value,
            "platform": row.platform,
            "target_type": row.target_type,
            "target_value": row.target_value,
            "enabled": row.enabled,
            "updated_at": row.updated_at,
        }
    return {
        "title": row.title,
        "community": row.community,
        "category": row.category,
        "is_active": row.is_active,
        "importance": row.importance,
        "updated_at": row.last_updated_at,
    }


@feeds_router.post("/{kind}/{ref_id}/token")
async def rebuild_token(
    kind: str,
    ref_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """重建 token：返回新地址，旧地址立即失效。"""
    _kind_or_400(kind)
    token = await rotate_feed_token(session, kind, ref_id)
    return {
        "kind": kind,
        "ref_id": ref_id,
        "feed_url": feed_url(resolve_base_url(request), kind, ref_id, token.token),
        "token": token.token,
        "token_status": token.status,
        "generation": token.generation,
    }


@feeds_router.delete("/{kind}/{ref_id}/token")
async def revoke_token(
    kind: str,
    ref_id: int,
    session: AsyncSession = Depends(get_session),
):
    """撤销 token：feed 停止输出，记录保留供排查。"""
    _kind_or_400(kind)
    token = await revoke_feed_token(session, kind, ref_id)
    return {
        "kind": kind,
        "ref_id": ref_id,
        "token_status": token.status,
        "revoked_at": token.revoked_at,
    }


@webhooks_router.get("")
async def list_webhooks(
    q: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Webhook 渠道一览：归属用户、推送范围与投递统计。"""
    conditions = []
    like = f"%{q.strip()}%" if q.strip() else None
    if like:
        conditions.append(
            User.email.like(like) | WebhookConfig.name.like(like) | WebhookConfig.url.like(like)
        )
    total = (
        await session.scalar(
            select(func.count(WebhookConfig.id))
            .join(User, User.id == WebhookConfig.user_id)
            .where(*conditions)
        )
        or 0
    )
    rows = (
        await session.execute(
            select(WebhookConfig, User.email, User.display_name)
            .join(User, User.id == WebhookConfig.user_id)
            .where(*conditions)
            .order_by(WebhookConfig.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    hook_ids = [hook.id for hook, _, _ in rows]
    stats = await _delivery_stats(session, hook_ids)
    return {
        "items": [
            {
                "id": hook.id,
                "user_id": hook.user_id,
                "owner_email": email,
                "owner_name": display_name,
                "name": hook.name,
                "url": hook.url,
                "enabled": hook.enabled,
                "tier_filter": hook.tier_filter,
                "created_at": hook.created_at,
                **stats.get(hook.id, {"sent": 0, "failed": 0, "last_delivery_at": None}),
            }
            for hook, email, display_name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def _delivery_stats(session: AsyncSession, hook_ids: list[int]) -> dict[int, dict]:
    if not hook_ids:
        return {}
    rows = await session.execute(
        select(
            WebhookDelivery.webhook_id,
            WebhookDelivery.status,
            func.count(WebhookDelivery.id),
            func.max(WebhookDelivery.created_at),
        )
        .where(WebhookDelivery.webhook_id.in_(hook_ids))
        .group_by(WebhookDelivery.webhook_id, WebhookDelivery.status)
    )
    stats: dict[int, dict] = {}
    for hook_id, delivery_status, count, last_at in rows:
        entry = stats.setdefault(
            hook_id, {"sent": 0, "failed": 0, "last_delivery_at": None}
        )
        if delivery_status == "sent":
            entry["sent"] = count
        elif delivery_status == "failed":
            entry["failed"] = count
        if last_at is not None and (
            entry["last_delivery_at"] is None or last_at > entry["last_delivery_at"]
        ):
            entry["last_delivery_at"] = last_at
    return stats


@webhooks_router.get("/deliveries")
async def list_deliveries(
    webhook_id: int | None = Query(default=None),
    delivery_status: str = Query(default="", pattern="^(|pending|sent|failed)$"),
    q: str = Query(default="", max_length=120, description="用户邮箱/渠道名/标题搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """投递记录：每次 POST 一行，attempts/状态码/错误可见。"""
    conditions = []
    if webhook_id is not None:
        conditions.append(WebhookDelivery.webhook_id == webhook_id)
    if delivery_status:
        conditions.append(WebhookDelivery.status == delivery_status)
    like = f"%{q.strip()}%" if q.strip() else None
    if like:
        conditions.append(
            User.email.like(like)
            | WebhookConfig.name.like(like)
            | WebhookDelivery.title.like(like)
        )
    total = (
        await session.scalar(
            select(func.count(WebhookDelivery.id))
            .join(
                WebhookConfig,
                WebhookConfig.id == WebhookDelivery.webhook_id,
            )
            .join(User, User.id == WebhookConfig.user_id)
            .where(*conditions)
        )
        or 0
    )
    rows = (
        await session.execute(
            select(WebhookDelivery, WebhookConfig.name, User.email)
            .join(WebhookConfig, WebhookConfig.id == WebhookDelivery.webhook_id)
            .join(User, User.id == WebhookConfig.user_id)
            .where(*conditions)
            .order_by(WebhookDelivery.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "webhook_id": row.webhook_id,
                "webhook_name": name,
                "owner_email": email,
                "kind": row.kind,
                "content_id": row.content_id,
                "title": row.title,
                "status": row.status,
                "attempts": row.attempts,
                "response_code": row.response_code,
                "error": row.error,
                "created_at": row.created_at,
                "sent_at": row.sent_at,
            }
            for row, name, email in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@webhooks_router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: int,
    session: AsyncSession = Depends(get_session),
):
    """手动重推：用已存 payload 再发一次（仍走 3 次重试上限）。"""
    row = await session.get(WebhookDelivery, delivery_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "投递记录不存在")
    webhook = await session.get(WebhookConfig, row.webhook_id)
    if webhook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook 配置不存在")
    result = await deliver_webhook(
        session, webhook, row.payload or {}, row=row
    )
    return {
        "id": result.id,
        "status": result.status,
        "attempts": result.attempts,
        "response_code": result.response_code,
        "error": result.error,
    }
