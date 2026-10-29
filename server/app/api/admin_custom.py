"""Admin 定制监控管理：全站用户的定制源巡查与处置。"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.api.admin import get_queue
from app.api.deps import require_admin
from app.db import get_session
from app.models import CustomFeed, CustomFeedItem, CustomFeedSource, CustomCollectRun, User
from app.clock import utcnow
from app.services.custom_policy import capacity, get_policy
from app.services.settings import get_setting, set_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/custom-feeds", dependencies=[Depends(require_admin)])


class UserQuotaOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_feeds: int | None = Field(default=None, ge=1, le=100)
    min_interval_minutes: int | None = Field(default=None, ge=5, le=1440)


class CustomPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open_beta: bool = False
    allowed_user_ids: list[PositiveInt] = Field(default_factory=list, max_length=1000)
    user_overrides: dict[str, UserQuotaOverride] = Field(default_factory=dict)
    max_feeds_per_user: int = Field(default=10, ge=1, le=100)
    min_interval_minutes: int = Field(default=30, ge=5, le=1440)
    max_active_sources: int = Field(default=60, ge=1, le=5000)
    custom_hourly_budget: int = Field(default=120, ge=1, le=10000)
    platform_hourly_budget: int = Field(default=360, ge=1, le=10000)


@router.get("/capacity")
async def custom_capacity(session: AsyncSession = Depends(get_session)):
    runs = (await session.execute(select(CustomCollectRun.status, func.count(), func.sum(CustomCollectRun.items_found),
        func.sum(CustomCollectRun.items_delivered)).where(CustomCollectRun.started_at >= utcnow()-timedelta(days=1))
        .group_by(CustomCollectRun.status))).all()
    sources = list(await session.scalars(select(CustomFeedSource).order_by(CustomFeedSource.id.desc()).limit(100)))
    budget = await get_setting(session, "collector_budget_bilibili") or {}
    if budget.get("hour") != utcnow().replace(minute=0, second=0, microsecond=0).isoformat():
        budget = {"total": 0, "custom": 0}
    return {"policy": await get_policy(session), "capacity": await capacity(session), "budget": budget,
        "usage_24h": [{"status": status, "tasks": count, "fetched_items": found or 0, "distributed_items": delivered or 0}
                      for status, count, found, delivered in runs],
        "sources": [{"id": s.id, "platform": s.platform, "kind": s.kind, "target_value": s.target_value,
                     "last_success_at": s.last_success_at, "next_run_at": s.next_run_at,
                     "consecutive_failures": s.consecutive_failures, "last_error": s.last_error} for s in sources],
        "notification_channels": ["email"], "planned_channels": ["astrbot"], "astrbot_requires_authorized_binding": True}


@router.put("/policy")
async def update_custom_policy(body: CustomPolicyBody, session: AsyncSession = Depends(get_session)):
    if body.custom_hourly_budget > body.platform_hourly_budget:
        raise HTTPException(400, "定制任务预算不能超过 B站全平台任务预算")
    if any(not key.isdigit() or int(key) < 1 for key in body.user_overrides):
        raise HTTPException(400, "用户配额覆盖项需使用用户 ID")
    await set_setting(session, "custom_feed_policy", body.model_dump(exclude_none=True))
    return await get_policy(session)


async def _feed_or_404(session: AsyncSession, feed_id: int) -> CustomFeed:
    feed = await session.get(CustomFeed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    return feed


@router.get("")
async def list_custom_feeds(
    q: str = Query(default="", max_length=120, description="监控名/目标/用户邮箱搜索"),
    kind: str = Query(default="", pattern="^(|uid|keyword|notices)$"),
    feed_status: str = Query(default="", pattern="^(|active|disabled)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    conditions = []
    if q.strip():
        like = f"%{q.strip()}%"
        conditions.append(
            User.email.like(like) | CustomFeed.name.like(like) | CustomFeed.target_value.like(like)
        )
    if kind:
        conditions.append(CustomFeed.kind == kind)
    if feed_status:
        conditions.append(CustomFeed.status == feed_status)

    item_count_sq = (
        select(CustomFeedItem.feed_id, func.count().label("cnt"))
        .group_by(CustomFeedItem.feed_id)
        .subquery()
    )
    total = await session.scalar(
        select(func.count(CustomFeed.id)).outerjoin(User, User.id == CustomFeed.user_id).where(*conditions)
    ) or 0
    rows = await session.execute(
        select(CustomFeed, User.email, User.display_name, func.coalesce(item_count_sq.c.cnt, 0))
        .outerjoin(User, User.id == CustomFeed.user_id)
        .outerjoin(item_count_sq, item_count_sq.c.feed_id == CustomFeed.id)
        .where(*conditions)
        .order_by(CustomFeed.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = []
    for feed, email, display_name, cnt in rows:
        items.append(
            {
                "id": feed.id,
                "name": feed.name,
                "kind": feed.kind,
                "platform": feed.platform,
                "target_value": feed.target_value,
                "min_views": feed.min_views,
                "push_enabled": feed.push_enabled,
                "is_public": feed.is_public,
                "enabled": feed.enabled,
                "status": feed.status,
                "owner_email": email,
                "owner_name": display_name,
                "item_count": cnt,
                "user_id": feed.user_id,
                "source_id": feed.source_id,
                "interval_minutes": feed.interval_minutes,
                "created_at": feed.created_at,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch("/{feed_id}")
async def update_custom_feed(
    feed_id: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """管理处置：停用/恢复（status）、上下架公开（is_public）、启停（enabled）。"""
    feed = await _feed_or_404(session, feed_id)
    actions: list[str] = []
    if "status" in body:
        new_status = str(body["status"])
        if new_status not in {"active", "disabled"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "status 只支持 active / disabled")
        if feed.status != new_status:
            feed.status = new_status
            actions.append(f"status->{new_status}")
    if "is_public" in body:
        feed.is_public = bool(body["is_public"])
        actions.append("is_public-updated")
    if "enabled" in body:
        feed.enabled = bool(body["enabled"])
        actions.append("enabled-updated")
    await session.commit()
    logger.info("admin updated custom feed %s: %s", feed_id, actions or "no-op")
    return {"id": feed.id, "status": feed.status, "is_public": feed.is_public, "enabled": feed.enabled, "actions": actions}


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_feed(
    feed_id: int,
    session: AsyncSession = Depends(get_session),
):
    feed = await _feed_or_404(session, feed_id)
    await session.delete(feed)
    await session.commit()


@router.post("/{feed_id}/collect", status_code=status.HTTP_202_ACCEPTED)
async def collect_custom_feed_now(
    feed_id: int,
    queue=Depends(get_queue),
    session: AsyncSession = Depends(get_session),
):
    """管理端手动触发一次抓取（复用定制轮询任务，立即入队）。"""
    feed = await _feed_or_404(session, feed_id)
    feed.next_run_at = None
    source = await session.get(CustomFeedSource, feed.source_id) if feed.source_id else None
    if source and not source.last_error:
        source.next_run_at = None  # 冷却、预算和租约仍由共享调度执行。
    await session.commit()
    job_id = await queue.enqueue_custom_sweep()
    if job_id is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "任务队列暂时不可用")
    return {"id": feed.id, "status": "queued"}
