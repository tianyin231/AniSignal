"""定制化公开 API：公开监控目录与监控详情（跨分区，登录可选）。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CustomFeed, CustomFeedItem, User
from app.services.auth import get_user_by_token

router = APIRouter(prefix="/api/custom")

SESSION_COOKIE = "anisignal_session"


async def _optional_user(request: Request, session: AsyncSession) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await get_user_by_token(session, token)


def _feed_out(feed: CustomFeed, owner_name: str | None, item_count: int | None = None) -> dict:
    return {
        "id": feed.id,
        "name": feed.name,
        "kind": feed.kind,
        "platform": feed.platform,
        "target_value": feed.target_value,
        "min_views": feed.min_views,
        "push_enabled": feed.push_enabled,
        "is_public": feed.is_public,
        "enabled": feed.enabled,
        "owner_name": owner_name,
        "item_count": item_count,
        "created_at": feed.created_at,
    }


@router.get("/feeds")
async def public_feeds(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """公开监控目录：全站用户公开到定制化频道的监控源。"""
    feed_scope = (
        CustomFeed.is_public.is_(True),
        CustomFeed.enabled.is_(True),
        CustomFeed.status == "active",
    )
    total = await session.scalar(select(func.count()).select_from(CustomFeed).where(*feed_scope)) or 0
    item_count_sq = (
        select(CustomFeedItem.feed_id, func.count().label("cnt"))
        .group_by(CustomFeedItem.feed_id)
        .subquery()
    )
    latest_sq = (
        select(
            CustomFeedItem.feed_id,
            func.max(CustomFeedItem.published_at).label("latest"),
        )
        .group_by(CustomFeedItem.feed_id)
        .subquery()
    )
    rows = await session.execute(
        select(CustomFeed, User.display_name, func.coalesce(item_count_sq.c.cnt, 0), latest_sq.c.latest)
        .join(User, User.id == CustomFeed.user_id)
        .outerjoin(item_count_sq, item_count_sq.c.feed_id == CustomFeed.id)
        .outerjoin(latest_sq, latest_sq.c.feed_id == CustomFeed.id)
        .where(*feed_scope)
        .order_by(CustomFeed.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = []
    for feed, owner, cnt, _latest in rows:
        out = _feed_out(feed, owner, cnt)
        items.append(out)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/feeds/{feed_id}")
async def feed_detail(
    feed_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """监控详情与条目：公开监控任何人可看；私有监控仅所有者可见。"""
    feed = await session.get(CustomFeed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    user = await _optional_user(request, session)
    is_owner = user is not None and user.id == feed.user_id
    if not feed.is_public and not is_owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    if feed.status != "active" and not is_owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    owner_name = await session.scalar(
        select(User.display_name).where(User.id == feed.user_id)
    )
    items = list(
        (
            await session.execute(
                select(CustomFeedItem)
                .where(CustomFeedItem.feed_id == feed_id)
                .order_by(CustomFeedItem.published_at.desc(), CustomFeedItem.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    return {
        **_feed_out(feed, owner_name),
        "is_owner": is_owner,
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "url": item.url,
                "cover": item.cover,
                "author_name": item.author_name,
                "view": item.view,
                "content_type": item.content_type,
                "published_at": item.published_at,
            }
            for item in items
        ],
    }
