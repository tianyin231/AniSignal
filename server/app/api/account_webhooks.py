"""账号中心 Webhook 渠道：用户自配只读推送地址（JSON，n8n 兼容）。

与邮件通知并行的一种通知渠道：命中用户启用中的关键词时，由调度任务 POST 到
这里配置的地址。地址只接受 http/https，且不允许指向本机或内网。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db import get_session
from app.models import (
    WEBHOOK_TIERS,
    WEBHOOK_TIER_ALL,
    User,
    WebhookConfig,
    WebhookDelivery,
)
from app.services.webhooks import is_safe_webhook_url, send_test

router = APIRouter(prefix="/api/account/webhooks", dependencies=[Depends(get_current_user)])

MAX_WEBHOOKS = 5
RECENT_DELIVERIES = 10


def _out(hook: WebhookConfig) -> dict:
    return {
        "id": hook.id,
        "name": hook.name,
        "url": hook.url,
        "enabled": hook.enabled,
        "tier_filter": hook.tier_filter,
        "created_at": hook.created_at,
    }


async def _hook_or_404(session: AsyncSession, user: User, webhook_id: int) -> WebhookConfig:
    hook = await session.get(WebhookConfig, webhook_id)
    if hook is None or hook.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook 不存在")
    return hook


@router.get("")
async def list_webhooks(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """我的 webhook 与最近投递记录。"""
    hooks = list(
        (
            await session.execute(
                select(WebhookConfig)
                .where(WebhookConfig.user_id == user.id)
                .order_by(WebhookConfig.id.desc())
            )
        ).scalars()
    )
    hook_ids = [hook.id for hook in hooks]
    deliveries: list[WebhookDelivery] = []
    if hook_ids:
        deliveries = list(
            (
                await session.execute(
                    select(WebhookDelivery)
                    .where(WebhookDelivery.webhook_id.in_(hook_ids))
                    .order_by(WebhookDelivery.id.desc())
                    .limit(RECENT_DELIVERIES)
                )
            ).scalars()
        )
    return {
        "items": [_out(hook) for hook in hooks],
        "limit": MAX_WEBHOOKS,
        "tier_options": [
            {"value": WEBHOOK_TIER_ALL, "label": "推送全部命中"},
            {"value": "important", "label": "只推重要命中"},
        ],
        "deliveries": [
            {
                "id": row.id,
                "webhook_id": row.webhook_id,
                "kind": row.kind,
                "title": row.title,
                "status": row.status,
                "attempts": row.attempts,
                "response_code": row.response_code,
                "error": row.error,
                "created_at": row.created_at,
                "sent_at": row.sent_at,
            }
            for row in deliveries
        ],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    url = str(body.get("url") or "").strip()
    name = str(body.get("name") or "").strip()[:64] or "Webhook"
    tier_filter = str(body.get("tier_filter") or WEBHOOK_TIER_ALL)
    if not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写 webhook 地址")
    if not is_safe_webhook_url(url):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "地址只支持 http/https，且不能指向本机或内网"
        )
    if tier_filter not in WEBHOOK_TIERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "推送范围只支持 important / all")
    count = await session.scalar(
        select(func.count(WebhookConfig.id)).where(WebhookConfig.user_id == user.id)
    )
    if (count or 0) >= MAX_WEBHOOKS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"最多 {MAX_WEBHOOKS} 个 webhook")
    hook = WebhookConfig(
        user_id=user.id, name=name, url=url, tier_filter=tier_filter, enabled=True
    )
    session.add(hook)
    await session.commit()
    await session.refresh(hook)
    return _out(hook)


@router.patch("/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    hook = await _hook_or_404(session, user, webhook_id)
    if "name" in body:
        name = str(body.get("name") or "").strip()[:64]
        if name:
            hook.name = name
    if "url" in body:
        url = str(body.get("url") or "").strip()
        if not is_safe_webhook_url(url):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "地址只支持 http/https，且不能指向本机或内网"
            )
        hook.url = url
    if "enabled" in body:
        hook.enabled = bool(body.get("enabled"))
    if "tier_filter" in body:
        tier_filter = str(body.get("tier_filter"))
        if tier_filter not in WEBHOOK_TIERS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "推送范围只支持 important / all")
        hook.tier_filter = tier_filter
    await session.commit()
    await session.refresh(hook)
    return _out(hook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    hook = await _hook_or_404(session, user, webhook_id)
    await session.delete(hook)
    await session.commit()


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """发送一条测试消息，确认地址与链路可用（同样记投递记录）。"""
    hook = await _hook_or_404(session, user, webhook_id)
    row = await send_test(session, hook)
    return {
        "id": row.id,
        "status": row.status,
        "attempts": row.attempts,
        "response_code": row.response_code,
        "error": row.error,
    }
