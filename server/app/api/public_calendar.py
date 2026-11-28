"""公开活动日历与可用兑换码接口（含 ICS 订阅流与单条日程导出）。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import (
    COMMUNITY_AGGREGATE_QUERY,
    PUBLIC_CACHE_SECONDS,
    public_site_base,
)
from app.clock import utcnow
from app.communities import (
    COMMUNITIES,
    CommunityOrAllSlug,
    community_conditions,
    is_all_community,
)
from app.db import get_session
from app.models import RawContent
from app.services.hidden_communities import request_hidden
from app.services.public_cache import CACHE_PREFIX, cached_dict
from app.services.urls import safe_link

router = APIRouter(prefix="/api")


@router.get("/calendar")
async def content_calendar(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    from app.services.calendar import calendar_groups

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}calendar:{community}" if not hidden else None
    return await cached_dict(
        cache,
        cache_key,
        PUBLIC_CACHE_SECONDS,
        lambda: calendar_groups(session, community, exclude=hidden),
    )


@router.get("/calendar/month")
async def monthly_calendar(
    request: Request,
    month: str = Query(pattern=r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$"),
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    from app.services.calendar import calendar_month

    return await calendar_month(session, community, month, exclude=request_hidden(request))


@router.get("/calendar/range")
async def calendar_range_api(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    start: str | None = Query(default=None, pattern=r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$"),
    days: int = Query(default=30, ge=7, le=90),
    days_back: int = Query(default=3, ge=0, le=30),
    session: AsyncSession = Depends(get_session),
):
    """跨月时间轴窗口日程（供活动日历甘特时间轴视图使用）。"""
    from app.services.calendar import calendar_range

    if start is not None:
        try:
            datetime.strptime(start, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(400, "无效日期") from exc

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = (
        f"{CACHE_PREFIX}calendar_range:{community}:{days}:{days_back}"
        if (not hidden and start is None and days in {14, 21, 30, 45} and days_back == 3)
        else None
    )
    return await cached_dict(
        cache,
        cache_key,
        PUBLIC_CACHE_SECONDS,
        lambda: calendar_range(
            session,
            community,
            start_date=start,
            days=days,
            days_back=days_back,
            exclude=hidden,
        ),
    )


@router.get("/calendar.ics")
async def calendar_subscription_feed(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """整分区/全站活动日历订阅流（支持 webcal:// 与系统日历自动刷新）。"""
    from app.services.calendar import calendar_entries, calendar_feed_ics

    cache = getattr(request.app.state, "redis", None)
    base = public_site_base(request)
    cache_key = f"{CACHE_PREFIX}calendar_ics:{community}"

    async def _load() -> dict:
        now = utcnow()
        entries = await calendar_entries(
            session,
            community,
            now=now,
            window_start=now - timedelta(days=14),
            window_end=now + timedelta(days=120),
        )
        all_mode = is_all_community(community)
        label = "全部分区" if all_mode else (COMMUNITIES.get(community).label if community in COMMUNITIES else community)
        labels = {key: meta.label for key, meta in COMMUNITIES.items()}
        ics = calendar_feed_ics(
            entries,
            cal_name=f"AniSignal 活动日历 · {label}",
            community_labels=labels,
            site_base=base,
            include_community_prefix=all_mode,
        )
        return {"ics": ics}

    payload = await cached_dict(cache, cache_key, PUBLIC_CACHE_SECONDS, _load)
    return Response(
        payload["ics"],
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="anisignal-{community}.ics"',
            "Cache-Control": "public, max-age=300",
        },
    )


@router.get("/calendar/{schedule_id}.ics")
async def export_calendar(schedule_id: int, session: AsyncSession = Depends(get_session)):
    from app.models import ContentSchedule
    from app.services.calendar import calendar_ics

    row = (await session.execute(
        select(ContentSchedule, RawContent.url)
        .join(RawContent, RawContent.id == ContentSchedule.raw_content_id)
        .where(ContentSchedule.id == schedule_id, RawContent.hidden.is_(False))
    )).first()
    if row is None:
        raise HTTPException(404, "日程不存在")
    return Response(calendar_ics(*row), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="anisignal-{schedule_id}.ics"'})


@router.get("/codes")
async def list_redeem_codes(
    request: Request,
    community: CommunityOrAllSlug = COMMUNITY_AGGREGATE_QUERY,
    session: AsyncSession = Depends(get_session),
):
    """可用兑换码列表（官方 + 社区 UP 主双轨抽取；过期或超 14 天未知有效期自动隐藏）。"""
    from app.models import RedeemCode
    from app.services.redeem_codes import active_redeem_code_conditions

    hidden = request_hidden(request)
    cache = getattr(request.app.state, "redis", None)
    cache_key = f"{CACHE_PREFIX}codes:{community}" if not hidden else None

    async def _load() -> dict:
        now = utcnow()
        conditions: list = [
            *active_redeem_code_conditions(now),
            *community_conditions(RedeemCode.community, community, hidden),
        ]
        rows = await session.execute(
            select(RedeemCode, RawContent.url, RawContent.published_at)
            .join(RawContent, RawContent.id == RedeemCode.raw_content_id)
            .where(*conditions)
            .order_by(
                (RedeemCode.source_kind == "official").desc(),
                RedeemCode.source_count.desc(),
                RedeemCode.created_at.desc(),
            )
            .limit(40)
        )
        items = [
            {
                "code": code.code,
                "community": code.community,
                "expires_at": code.expires_at,
                "created_at": code.created_at,
                "published_at": published_at,
                "source_kind": code.source_kind or "official",
                "source_count": code.source_count or 1,
                "server": code.server,
                "reward": code.reward,
                "expiry_estimated": bool(code.expiry_estimated),
                # 兑换码页把 url 直接绑到 href，脏 scheme 不下发
                "url": safe_link(url),
            }
            for code, url, published_at in rows
        ]
        return {"items": items, "total": len(items)}

    return await cached_dict(cache, cache_key, PUBLIC_CACHE_SECONDS, _load)
