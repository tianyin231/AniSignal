"""定制化公开 API：公开监控目录与监控详情（跨分区，登录可选）。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.custom_presets import custom_preset
from app.models import CustomFeed, CustomFeedItem, User
from app.services.auth import get_user_by_token
from app.services.urls import safe_link, safe_media_url

router = APIRouter(prefix="/api/custom")

SESSION_COOKIE = "anisignal_session"

# 公开端点无鉴权：page 不封顶时深分页就是免费的「全表扫」入口。上界按偏移量算，
# 这样和 page_size 的取值无关（页大时页数上限自然更小）。
MAX_PAGE_OFFSET = 10_000
# 目录卡片上预览的最新条目数
LATEST_PREVIEW = 2


def _check_page_depth(page: int, page_size: int) -> None:
    if (page - 1) * page_size > MAX_PAGE_OFFSET:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "分页过深，请缩小页码或使用筛选条件",
        )


async def _optional_user(request: Request, session: AsyncSession) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await get_user_by_token(session, token)


def _default_source_url(feed: CustomFeed) -> str | None:
    if feed.platform == "bilibili" and feed.kind == "uid" and feed.target_value.isdigit():
        return f"https://space.bilibili.com/{feed.target_value}"
    if feed.platform == "youtube" and feed.kind == "channel_rss":
        return safe_link(f"https://www.youtube.com/channel/{feed.target_value}")
    if feed.platform == "rss" and feed.kind == "feed":
        return safe_link(feed.target_value)
    return None


def _feed_out(feed: CustomFeed, owner_name: str | None, item_count: int | None = None) -> dict:
    preset = custom_preset(feed.platform, feed.kind, feed.target_value) if feed.user_id is None else {}
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
        "status": feed.status,
        "owner_name": owner_name if feed.user_id is not None else "AniSignal · 站点维护",
        "site_managed": feed.user_id is None,
        "community": preset.get("community"),
        "target_label": preset.get("target_label", feed.target_value),
        "description": preset.get("description", ""),
        "source_url": preset.get("source_url") or _default_source_url(feed),
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
    _check_page_depth(page, page_size)
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
        .outerjoin(User, User.id == CustomFeed.user_id)
        .outerjoin(item_count_sq, item_count_sq.c.feed_id == CustomFeed.id)
        .outerjoin(latest_sq, latest_sq.c.feed_id == CustomFeed.id)
        .where(*feed_scope)
        .order_by(CustomFeed.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = rows.all()
    previews = await _latest_previews(session, [feed.id for feed, *_ in rows])
    items = [
        {**_feed_out(feed, owner, cnt), "latest_at": latest, "latest_items": previews.get(feed.id, [])}
        for feed, owner, cnt, latest in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _latest_previews(session: AsyncSession, feed_ids: list[int]) -> dict[int, list[dict]]:
    """每个监控最新的几条（窗口函数一次取完，避免逐个监控查询）。"""
    if not feed_ids:
        return {}
    ranked = (
        select(
            CustomFeedItem.feed_id,
            CustomFeedItem.title,
            CustomFeedItem.url,
            CustomFeedItem.published_at,
            func.row_number()
            .over(
                partition_by=CustomFeedItem.feed_id,
                order_by=(CustomFeedItem.published_at.desc(), CustomFeedItem.id.desc()),
            )
            .label("rank"),
        )
        .where(CustomFeedItem.feed_id.in_(feed_ids))
        .subquery()
    )
    previews: dict[int, list[dict]] = {}
    result = await session.execute(
        select(ranked.c.feed_id, ranked.c.title, ranked.c.url, ranked.c.published_at)
        .where(ranked.c.rank <= LATEST_PREVIEW)
        .order_by(ranked.c.feed_id, ranked.c.rank)
    )
    for feed_id, title, url, published_at in result:
        previews.setdefault(feed_id, []).append(
            {"title": title, "url": safe_link(url), "published_at": published_at}
        )
    return previews


@router.get("/feeds/{feed_id}")
async def feed_detail(
    feed_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """监控详情与条目：公开监控任何人可看；私有监控仅所有者可见。"""
    _check_page_depth(page, page_size)
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
    total = await session.scalar(
        select(func.count(CustomFeedItem.id)).where(CustomFeedItem.feed_id == feed_id)
    ) or 0
    items = list(
        (
            await session.execute(
                select(CustomFeedItem)
                .where(CustomFeedItem.feed_id == feed_id)
                .order_by(CustomFeedItem.published_at.desc(), CustomFeedItem.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
    )
    return {
        **_feed_out(feed, owner_name, total),
        "total": total,
        "page": page,
        "page_size": page_size,
        "is_owner": is_owner,
        "items": [
            {
                "id": item.id,
                "title": item.title,
                # 监控详情页把 url/cover 直接绑到 href/src，脏值不下发
                "url": safe_link(item.url),
                "cover": safe_media_url(item.cover),
                "author_name": item.author_name,
                "view": item.view,
                "content_type": item.content_type,
                "platform": item.platform,
                "published_at": item.published_at,
            }
            for item in items
        ],
    }
