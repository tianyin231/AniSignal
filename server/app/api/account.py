"""账号中心 API：关键词监控 CRUD、个性化关注流、账号概览。"""

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import literal, or_, select, func, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, sync_hidden_cookie
from app.clock import utcnow
from app.communities import COMMUNITIES, DEFAULT_COMMUNITY
from app.db import get_session
from app.api.deps import get_queue
from app.models import (
    ContentVersion,
    CustomFeed,
    EventRevision,
    ProductFulfillmentRevision,
    NotificationDelivery,
    RawContent,
    Source,
    User,
    UserKeyword,
    UserWebhook,
)
from app.schemas.api import PublicContentItem, PublicContentList
from app.services.notifications import beijing_today, create_delivery, enabled_keywords
from app.services.feed_preferences import preference_conditions
from app.services.hidden_communities import PLATFORM_TOKENS, account_blocks, normalize_hidden, normalize_platforms
from app.services.queue import BaseQueue
from app.services.xiaoheihe import (
    XIAOHEIHE_ACCOUNT_KIND,
    XIAOHEIHE_INTERVAL_MINUTES,
    XIAOHEIHE_KEY,
    XIAOHEIHE_KEYWORD_KIND,
    XIAOHEIHE_KIND,
    xiaoheihe_capabilities,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", dependencies=[Depends(get_current_user)])

MAX_KEYWORDS = 10


def _title_condition(keyword: str):
    return func.lower(ContentVersion.title).contains(keyword.lower(), autoescape=True)


def _join_latest(query):
    return query.select_from(RawContent).join(
        ContentVersion, ContentVersion.id == RawContent.latest_version_id
    )


async def _keyword_counts(
    session: AsyncSession, keywords: list[UserKeyword], preferences: dict | None = None
) -> dict[str, int]:
    """每个关键词当前命中的内容条数（单次 union_all 查询）。"""
    if not keywords:
        return {}
    queries = [
        _join_latest(select(
            literal(k.keyword).label("keyword"),
            func.count(RawContent.id).label("cnt"),
        ))
        .where(_title_condition(k.keyword), RawContent.hidden.is_(False), *preference_conditions(preferences))
        for k in keywords
    ]
    rows = await session.execute(union_all(*queries))
    return {row.keyword: row.cnt for row in rows}


@router.get("/overview")
async def overview(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """账号中心首屏：基础信息 + 关键词统计。"""
    keywords = list(
        (
            await session.execute(
                select(UserKeyword)
                .where(UserKeyword.user_id == user.id)
                .order_by(UserKeyword.id.desc())
            )
        ).scalars()
    )
    feed_total = 0
    if any(k.enabled for k in keywords):
        feed_total = (
            await session.scalar(
                _join_latest(select(func.count(RawContent.id)))
                .where(
                    or_(*[_title_condition(k.keyword) for k in keywords if k.enabled]),
                    RawContent.hidden.is_(False),
                    *preference_conditions(user.feed_preferences),
                )
            )
            or 0
        )
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar": user.avatar,
        "is_verified": user.is_verified,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "keywords_total": len(keywords),
        "keywords_enabled": sum(1 for k in keywords if k.enabled),
        "feed_total": feed_total,
    }


@router.get("/feed-preferences")
async def get_feed_preferences(user: User = Depends(get_current_user)):
    preferences = user.feed_preferences or {}
    return {"communities": preferences.get("communities", []), "exclude_keywords": preferences.get("exclude_keywords", [])}


@router.put("/feed-preferences")
async def update_feed_preferences(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    communities = body.get("communities", [])
    excluded = body.get("exclude_keywords", [])
    if not isinstance(communities, list) or any(not isinstance(c, str) or c not in COMMUNITIES for c in communities):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择有效游戏分区")
    if not isinstance(excluded, list) or len(excluded) > 20 or any(not isinstance(k, str) or not k.strip() or len(k.strip()) > 64 for k in excluded):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "最多 20 个排除词，每个 1–64 字")
    preferences = {"communities": list(dict.fromkeys(communities)), "exclude_keywords": list(dict.fromkeys(k.strip() for k in excluded))}
    # 偏好改变后，手动早报不能复用旧筛选条件生成的快照。保留同一字段里的网站屏蔽设置。
    user.feed_preferences = {**(user.feed_preferences or {}), **preferences, "updated_at": utcnow().isoformat()}
    await session.commit()
    return preferences


def _blocks_body(blocks) -> dict:
    return {"communities": list(blocks.communities), "platforms": list(blocks.platforms)}


@router.get("/hidden-communities")
async def get_hidden_communities(user: User = Depends(get_current_user)):
    return _blocks_body(account_blocks(user))


@router.put("/hidden-communities")
async def update_hidden_communities(
    body: dict,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """网站浏览屏蔽：分区只隐藏跨分区聚合列表，来源平台按游戏 / AI 两组过滤；不影响邮件、Bot 与关注流。

    请求体缺少某个字段时保留该项原设置。
    """
    current = account_blocks(user)
    values = body.get("communities", list(current.communities))
    if not isinstance(values, list) or any(not isinstance(c, str) or c not in COMMUNITIES for c in values):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择有效游戏分区")
    hidden = normalize_hidden(values)
    if len(hidden) >= len(COMMUNITIES):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少保留一个分区")
    tokens = body.get("platforms", list(current.platforms))
    if not isinstance(tokens, list) or any(not isinstance(t, str) or t not in PLATFORM_TOKENS for t in tokens):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择有效来源平台")
    platforms = normalize_platforms(tokens)
    # 不更新 updated_at：它只用于邮件早报快照，屏蔽不改变邮件内容。
    user.feed_preferences = {**(user.feed_preferences or {}), "hidden_communities": hidden, "hidden_platforms": platforms}
    await session.commit()
    sync_hidden_cookie(request, response, user)
    return _blocks_body(account_blocks(user))


CALENDAR_TIMEZONES = frozenset({
    "Asia/Shanghai",
    "Asia/Tokyo",
    "America/New_York",
    "Europe/Berlin",
    "UTC",
    "local",
})
DEFAULT_CALENDAR_TIMEZONE = "Asia/Shanghai"
MAX_CLAIMED_CODES = 200
MAX_COMPLETED_SCHEDULES = 300


def _read_calendar_state(user: User) -> dict:
    raw = (user.feed_preferences or {}).get("calendar_state")
    if not isinstance(raw, dict):
        return {"claimed_codes": [], "completed_schedules": [], "timezone": DEFAULT_CALENDAR_TIMEZONE}
    claimed = [c for c in raw.get("claimed_codes", []) if isinstance(c, str) and c]
    completed = [i for i in raw.get("completed_schedules", []) if type(i) is int and i > 0]
    tz = raw.get("timezone")
    if not isinstance(tz, str) or tz not in CALENDAR_TIMEZONES:
        tz = DEFAULT_CALENDAR_TIMEZONE
    return {
        "claimed_codes": claimed[-MAX_CLAIMED_CODES:],
        "completed_schedules": completed[-MAX_COMPLETED_SCHEDULES:],
        "timezone": tz,
    }


@router.get("/calendar-state")
async def get_calendar_state(user: User = Depends(get_current_user)):
    """个人日历打卡状态（已领兑换码、已完成日程、时区偏好）。"""
    return _read_calendar_state(user)


@router.put("/calendar-state")
async def update_calendar_state(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """更新个人日历打卡与时区偏好；缺少的字段保留原值，不影响关注流与屏蔽设置。"""
    current = _read_calendar_state(user)
    next_state = dict(current)

    if "claimed_codes" in body:
        raw_codes = body["claimed_codes"]
        if not isinstance(raw_codes, list) or len(raw_codes) > 1000:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "已领兑换码格式无效")
        cleaned_codes: list[str] = []
        seen_codes: set[str] = set()
        for item in raw_codes:
            if not isinstance(item, str):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "兑换码标识必须是字符串")
            token = item.strip()
            if not token or len(token) > 80:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "兑换码标识长度需在 1–80 字符之间")
            if token not in seen_codes:
                seen_codes.add(token)
                cleaned_codes.append(token)
        next_state["claimed_codes"] = cleaned_codes[-MAX_CLAIMED_CODES:]

    if "completed_schedules" in body:
        raw_ids = body["completed_schedules"]
        if not isinstance(raw_ids, list) or len(raw_ids) > 1000:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "已完成日程格式无效")
        cleaned_ids: list[int] = []
        seen_ids: set[int] = set()
        for item in raw_ids:
            if type(item) is not int or item <= 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "日程 ID 必须是正整数")
            if item not in seen_ids:
                seen_ids.add(item)
                cleaned_ids.append(item)
        next_state["completed_schedules"] = cleaned_ids[-MAX_COMPLETED_SCHEDULES:]

    if "timezone" in body:
        tz = body["timezone"]
        if not isinstance(tz, str) or tz not in CALENDAR_TIMEZONES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择支持的时区")
        next_state["timezone"] = tz

    # 不更新 updated_at：它只用于邮件早报快照，打卡不改变早报内容。
    user.feed_preferences = {**(user.feed_preferences or {}), "calendar_state": next_state}
    await session.commit()
    return next_state


@router.get("/notifications")
async def notification_status(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = list(
        (
            await session.execute(
                select(NotificationDelivery)
                .where(NotificationDelivery.user_id == user.id)
                .order_by(NotificationDelivery.id.desc())
                .limit(8)
            )
        ).scalars()
    )
    return {
        "enabled": user.notification_enabled,
        "instant_enabled": user.instant_notification_enabled,
        "revision_enabled": user.revision_notification_enabled,
        "schedule": "每天 08:00（北京时间）",
        "records": [
            {
                "id": row.id,
                "kind": row.kind,
                "source": row.source,
                "period_date": row.period_date,
                "status": row.status,
                "subject": row.subject,
                "matched_keywords": row.matched_keywords or [],
                "content_count": len(row.content_ids or []),
                "error": row.error,
                "created_at": row.created_at,
                "sent_at": row.sent_at,
            }
            for row in rows
        ],
    }


@router.put("/notifications")
async def update_notification_preference(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # 与即时通知准备共用用户行锁，防止恢复时的新基线被旧批次覆盖。
    user = await session.get(User, user.id, populate_existing=True, with_for_update=True)
    was_active = user.notification_enabled and user.instant_notification_enabled
    was_rev_active = user.notification_enabled and user.revision_notification_enabled
    for field in ("enabled", "instant_enabled", "revision_enabled"):
        if field in body and not isinstance(body[field], bool):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "通知开关必须是布尔值")
    if body.get("instant_enabled"):
        if not user.is_verified:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先验证邮箱")
        if not await enabled_keywords(session, user.id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先启用至少一个关键词")
    if body.get("revision_enabled") and not user.is_verified:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先验证邮箱")
    if "enabled" in body:
        user.notification_enabled = body["enabled"]
    if "instant_enabled" in body:
        user.instant_notification_enabled = body["instant_enabled"]
    if "revision_enabled" in body:
        user.revision_notification_enabled = body["revision_enabled"]
    if user.notification_enabled and user.instant_notification_enabled and not was_active:
        # MySQL 的普通查询可能沿用鉴权阶段的旧快照；锁定读取得真正的最新基线。
        user.instant_notification_cursor = await session.scalar(
            select(EventRevision.id).order_by(EventRevision.id.desc()).limit(1).with_for_update()
        ) or 0
        user.instant_fulfillment_cursor = await session.scalar(
            select(ProductFulfillmentRevision.id).order_by(ProductFulfillmentRevision.id.desc()).limit(1).with_for_update()
        ) or 0
    if user.notification_enabled and user.revision_notification_enabled and not was_rev_active:
        user.instant_revision_cursor = await session.scalar(
            select(ContentVersion.id).order_by(ContentVersion.id.desc()).limit(1).with_for_update()
        ) or 0
    if not user.notification_enabled:
        await session.execute(update(NotificationDelivery).where(
            NotificationDelivery.user_id == user.id, NotificationDelivery.kind == "instant",
            NotificationDelivery.status == "pending",
        ).values(status="skipped", error="用户已关闭邮件通知"))
    else:
        if not user.instant_notification_enabled:
            await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.user_id == user.id, NotificationDelivery.kind == "instant",
                NotificationDelivery.status == "pending",
                ~NotificationDelivery.dedupe_key.startswith("instant-revision:"),
            ).values(status="skipped", error="用户已关闭即时邮件通知"))
        if not user.revision_notification_enabled:
            await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.user_id == user.id, NotificationDelivery.kind == "instant",
                NotificationDelivery.status == "pending",
                NotificationDelivery.dedupe_key.startswith("instant-revision:"),
            ).values(status="skipped", error="用户已关闭官方公告修订提醒"))
    await session.commit()
    return {
        "enabled": user.notification_enabled,
        "instant_enabled": user.instant_notification_enabled,
        "revision_enabled": user.revision_notification_enabled,
        "schedule": "每天 08:00（北京时间）",
    }


@router.post("/notifications/send", status_code=status.HTTP_202_ACCEPTED)
async def request_notification(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    queue: BaseQueue = Depends(get_queue),
):
    kind = str(body.get("kind") or "")
    if kind not in {"morning", "ai"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "通知类型只支持 morning / ai")
    if not user.is_verified:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先验证邮箱")
    if not user.notification_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先启用邮件通知")
    if kind == "ai" and not await enabled_keywords(session, user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先启用至少一个关键词")
    pending = await session.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.user_id == user.id,
            NotificationDelivery.kind == kind,
            NotificationDelivery.status == "pending",
        )
    )
    if pending is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "同类通知正在生成，请稍后查看")
    delivery = await create_delivery(
        session,
        user.id,
        kind,
        "manual",
        day=beijing_today(),
    )
    job_id = await queue.enqueue_notification(delivery.id)
    if job_id is None:
        delivery.status = "failed"
        delivery.error = "任务队列暂时不可用"
        await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "任务队列暂时不可用")
    return {
        "delivery_id": delivery.id,
        "status": "pending",
        "message": "AI 精炼通知已进入队列，通常需要几分钟" if kind == "ai" else "今日早报已进入发送队列",
    }


@router.get("/keywords")
async def list_keywords(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    keywords = list(
        (
            await session.execute(
                select(UserKeyword)
                .where(UserKeyword.user_id == user.id)
                .order_by(UserKeyword.id.desc())
            )
        ).scalars()
    )
    counts = await _keyword_counts(session, keywords, user.feed_preferences)
    return [
        {
            "id": k.id,
            "keyword": k.keyword,
            "enabled": k.enabled,
            "matches": counts.get(k.keyword, 0),
            "created_at": k.created_at,
        }
        for k in keywords
    ]


@router.post("/keywords", status_code=status.HTTP_201_CREATED)
async def add_keyword(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    keyword = str(body.get("keyword", "")).strip()
    if not keyword or len(keyword) > 64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "关键词不能为空且不超过 64 字")
    count = await session.scalar(
        select(func.count(UserKeyword.id)).where(UserKeyword.user_id == user.id)
    )
    if (count or 0) >= MAX_KEYWORDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"最多 {MAX_KEYWORDS} 个关键词（基础订阅开放后可扩展）"
        )
    duplicate = await session.scalar(
        select(UserKeyword).where(
            UserKeyword.user_id == user.id, UserKeyword.keyword == keyword
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "关键词已存在")
    row = UserKeyword(user_id=user.id, keyword=keyword)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "keyword": row.keyword, "enabled": row.enabled, "created_at": row.created_at}


@router.put("/keywords/{keyword_id}")
async def toggle_keyword(
    keyword_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(UserKeyword, keyword_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关键词不存在")
    if "enabled" in body:
        row.enabled = bool(body["enabled"])
    await session.commit()
    return {"id": row.id, "keyword": row.keyword, "enabled": row.enabled}


@router.delete("/keywords/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyword(
    keyword_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(UserKeyword, keyword_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关键词不存在")
    await session.delete(row)
    await session.commit()


@router.get("/feed", response_model=PublicContentList)
async def my_feed(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    keyword_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """我的关注流：命中启用关键词的最新内容（标题匹配）。

    keyword_id 提供时只看该关键词（仍须属于当前用户）。
    """
    keywords = list(
        (
            await session.execute(
                select(UserKeyword).where(
                    UserKeyword.user_id == user.id, UserKeyword.enabled.is_(True)
                )
            )
        ).scalars()
    )
    if keyword_id is not None:
        owned = await session.get(UserKeyword, keyword_id)
        if owned is None or owned.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "关键词不存在")
        keywords = [owned]
    if not keywords:
        return PublicContentList(items=[], total=0, new_today=0)

    conditions = [or_(*[_title_condition(k.keyword) for k in keywords]), RawContent.hidden.is_(False), *preference_conditions(user.feed_preferences)]
    joined = _join_latest(select(func.count(RawContent.id))).where(*conditions)
    total = await session.scalar(joined) or 0
    new_today = (
        await session.scalar(
            joined.where(RawContent.collected_at >= utcnow() - timedelta(hours=24))
        )
        or 0
    )

    rows = await session.execute(
        _join_latest(select(RawContent, Source, ContentVersion))
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(*conditions)
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items: list[PublicContentItem] = []
    for raw, source, version in rows:
        normalized = version.normalized_json or {}
        media = normalized.get("media") or []
        title = version.title or raw.external_id
        lowered = title.lower()
        matched_user_keywords = [k.keyword for k in keywords if k.keyword.lower() in lowered]
        items.append(
            PublicContentItem(
                id=raw.id,
                platform=raw.platform,
                content_type=raw.content_type,
                external_id=raw.external_id,
                url=raw.url,
                title=title,
                text_excerpt=_excerpt(version.text or str(normalized.get("text") or "")),
                published_at=raw.published_at,
                collected_at=raw.collected_at,
                source_name=(source.name if source else None),
                identity=(source.identity if source else "unknown"),
                metrics=None,
                cover=media[0] if isinstance(media, list) and media else None,
                preview_mode=(
                    "full"
                    if (version.text or str(normalized.get("text") or "")).strip()
                    else ("partial" if media else "external_only")
                ),
                matched_keywords=matched_user_keywords,
                community=(getattr(raw, "community", None) or DEFAULT_COMMUNITY),
            )
        )
    return PublicContentList(items=items, total=total, new_today=new_today)


def _excerpt(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if "<" in text and ">" in text:
        import bleach

        text = bleach.clean(text, tags=[], strip=True).strip()
    return text[:140] + ("…" if len(text) > 140 else "")


# ---------- 定制监控（用户自建 B站关键词/UP主、RSS/Atom、YouTube 频道抓取源） ----------

import re as _re

_KIND_DEFAULT_PLATFORM = {
    "uid": "bilibili",
    "keyword": "bilibili",
    "feed": "rss",
    "channel_rss": "youtube",
    "steam_app": "steam",
    "hot_posts": "xiaoheihe",
    XIAOHEIHE_ACCOUNT_KIND: XIAOHEIHE_KEY,
    XIAOHEIHE_KEYWORD_KIND: XIAOHEIHE_KEY,
}
_YT_CHANNEL_RE = _re.compile(r"^(?:UC[A-Za-z0-9_-]{20,30}|@[A-Za-z0-9._-]{3,30})$")


def _normalize_youtube_channel(raw: str) -> str:
    from app.schemas.api import normalize_youtube_channel_input

    return normalize_youtube_channel_input(raw)


def _validate_custom_target(kind: str, target: str) -> str:
    if kind == "hot_posts" and target != "hot":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择小黑盒热帖")
    if kind == XIAOHEIHE_ACCOUNT_KIND:
        from app.parsers.xiaoheihe import account_target

        try:
            return account_target(target)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    if kind == XIAOHEIHE_KEYWORD_KIND:
        if len(target) > 64 or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "小黑盒关键词需为 1 至 64 字且不含控制字符")
        return target
    if kind == "uid":
        if not target.isdigit():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "UID 只能是数字")
        return target
    if kind == "channel_rss":
        chan = _normalize_youtube_channel(target)
        if not _YT_CHANNEL_RE.fullmatch(chan):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写 YouTube 频道链接、@handle 或以 UC 开头的频道 ID")
        return chan
    if kind == "steam_app":
        from app.parsers.steam import extract_app_id

        try:
            return extract_app_id(target)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return target


async def _resolve_custom_target(session: AsyncSession, kind: str, target: str) -> str:
    """创建或修改时将分享标识解析成平台的稳定账号 ID。"""
    if kind == XIAOHEIHE_ACCOUNT_KIND and not target.isdecimal():
        from app.collectors.base import CollectorError
        from app.collectors.xiaoheihe import resolve_account_uid

        try:
            return await resolve_account_uid(target, session=session)
        except CollectorError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "暂时无法解析小黑盒主页，请稍后重试或填写数字 UID") from None
    if kind != "channel_rss" or not target.startswith("@"):
        return target
    from app.collectors.base import CollectorError
    from app.collectors.youtube_rss import resolve_youtube_handle

    try:
        return await resolve_youtube_handle(session, target)
    except CollectorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


async def _ensure_safe_custom_target(kind: str, target: str) -> None:
    if kind == "feed":
        from app.collectors.base import CollectorError
        from app.collectors.rss import public_feed_url

        try:
            await public_feed_url(target)
        except CollectorError as exc:
            from app.services.collect_errors import user_facing_error

            # 普通用户看不到也改不了服务端配置，提示里不带解析地址和环境变量名。
            raise HTTPException(status.HTTP_400_BAD_REQUEST, user_facing_error("rss", str(exc))) from exc


def _validate_custom_payload(body: dict, *, partial: bool = False, default_kind: str = "") -> dict:
    from app.services.custom_feeds import CUSTOM_SPECS

    kind = str(body.get("kind") or "").strip()
    if kind and kind not in _KIND_DEFAULT_PLATFORM:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持的监控类型")
    effective_kind = kind or default_kind
    raw_platform = body.get("platform")
    platform = str(raw_platform).strip() if raw_platform is not None else (_KIND_DEFAULT_PLATFORM.get(kind, "bilibili") if not partial else "")
    if kind and platform and (platform, kind) not in CUSTOM_SPECS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不支持的平台与监控类型组合：{platform}/{kind}")
    if not kind and platform and platform not in _KIND_DEFAULT_PLATFORM.values():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持的监控平台")
    name = str(body.get("name") or "").strip()
    if ("name" in body or not partial) and not 1 <= len(name) <= 64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控名称需为 1 至 64 字")
    target = str(body.get("target_value") or "").strip()
    if effective_kind and target:
        target = _validate_custom_target(effective_kind, target)
    if ("target_value" in body or not partial) and not 1 <= len(target) <= 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控目标需为 1 至 128 字")
    min_views = body.get("min_views")
    if min_views is not None:
        try:
            parsed_views = int(min_views)
            if isinstance(min_views, bool) or (isinstance(min_views, float) and min_views != parsed_views):
                raise ValueError
            if not 0 <= parsed_views <= 10_000_000:
                raise ValueError
            min_views = parsed_views
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "播放量门槛需为 0 至 10000000 的整数")
    interval = body.get("interval_minutes")
    if interval is not None and (type(interval) is not int or not 1 <= interval <= 1440):
        raise HTTPException(400, "采集周期需为 1 至 1440 分钟的整数")
    filters = body.get("filters")
    if filters is not None:
        if (not isinstance(filters, dict) or set(filters) - {"include", "exclude"}
            or any(not isinstance(values, list) or len(values) > 10
                   or any(not isinstance(term, str) or not 1 <= len(term.strip()) <= 64 for term in values)
                   for values in filters.values())):
            raise HTTPException(400, "包含词、排除词各最多 10 个，每个 1 至 64 字")
        filters = {key: list(dict.fromkeys(term.strip() for term in values)) for key, values in filters.items()}
    return {
        "name": name,
        "kind": kind,
        "platform": platform,
        "target_value": target,
        "min_views": min_views,
        "push_enabled": body.get("push_enabled"),
        "is_public": body.get("is_public"),
        "enabled": body.get("enabled"),
        "interval_minutes": interval,
        "filters": filters,
    }


@router.get("/custom-feeds")
async def my_custom_feeds(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """我的定制监控（公开与私有全部返回，仅本人可见）。"""
    from app.services.custom_feeds import count_feed_items

    feeds = list(
        (
            await session.execute(
                select(CustomFeed)
                .where(CustomFeed.user_id == user.id)
                .order_by(CustomFeed.id.desc())
            )
        ).scalars()
    )
    counts = await count_feed_items(session, [f.id for f in feeds])
    from app.models import CustomFeedSource
    from app.services.collect_errors import user_facing_error

    source_ids = {f.source_id for f in feeds if f.source_id}
    errors = dict((await session.execute(
        select(CustomFeedSource.id, CustomFeedSource.last_error).where(CustomFeedSource.id.in_(source_ids))
    )).all()) if source_ids else {}
    return [
        {
            "id": f.id,
            "name": f.name,
            "kind": f.kind,
            "platform": f.platform,
            "target_value": f.target_value,
            "min_views": f.min_views,
            "push_enabled": f.push_enabled,
            "is_public": f.is_public,
            "enabled": f.enabled,
            "status": f.status,
            "item_count": counts.get(f.id, 0),
            "last_collected_at": f.last_collected_at,
            # 只给友好文案，不回显上游 URL 或服务端配置项。
            "last_error": user_facing_error(f.platform, errors.get(f.source_id)),
            "interval_minutes": f.interval_minutes,
            "filters": f.filters,
            "created_at": f.created_at,
        }
        for f in feeds
    ]


@router.get("/custom-quota")
async def custom_quota(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    from app.services.custom_policy import user_quota

    xhh = await xiaoheihe_capabilities(session)
    return {
        **(await user_quota(session, user.id)),
        "notification_channels": ["email", "astrbot"],
        "planned_channels": [],
        "astrbot_requires_authorized_binding": True,
        "xiaoheihe_keyword_enabled": xhh["keyword_enabled"],
        "xiaoheihe_hot_enabled": xhh["hot_enabled"],
    }


@router.post("/custom-feeds", status_code=status.HTTP_201_CREATED)
async def create_custom_feed(
    body: dict,
    user: User = Depends(get_current_user),
    queue: BaseQueue = Depends(get_queue),
    session: AsyncSession = Depends(get_session),
):
    """新建定制监控；额度与最短周期由账号公测配置决定。"""
    payload = _validate_custom_payload(body)
    if not payload["name"] or not payload["kind"] or not payload["target_value"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "名称、监控类型与目标均不能为空")
    if payload["kind"] in {XIAOHEIHE_KEYWORD_KIND, XIAOHEIHE_KIND}:
        caps = await xiaoheihe_capabilities(session)
        if payload["kind"] == XIAOHEIHE_KEYWORD_KIND and not caps["keyword_enabled"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "管理员尚未配置小黑盒关键词采集网关")
        if payload["kind"] == XIAOHEIHE_KIND and not caps["hot_enabled"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "管理员尚未配置小黑盒热帖采集源")
    await _ensure_safe_custom_target(payload["kind"], payload["target_value"])
    payload["target_value"] = await _resolve_custom_target(session, payload["kind"], payload["target_value"])
    from app.services.custom_policy import check_admission, shared_source, user_quota, request_interval
    quota = await user_quota(session, user.id)
    interval = payload["interval_minutes"] or (
        XIAOHEIHE_INTERVAL_MINUTES if payload["kind"] == XIAOHEIHE_KIND else quota["min_interval_minutes"]
    )
    await check_admission(session, user.id, payload["platform"], payload["kind"], payload["target_value"], interval)
    if payload["platform"] == XIAOHEIHE_KEY and await session.scalar(select(CustomFeed.id).where(
        CustomFeed.user_id == user.id, CustomFeed.platform == XIAOHEIHE_KEY,
        CustomFeed.kind == payload["kind"], CustomFeed.target_value == payload["target_value"],
    ).limit(1)):
        messages = {
            XIAOHEIHE_ACCOUNT_KIND: "已监控该小黑盒账号",
            XIAOHEIHE_KEYWORD_KIND: "已监控该小黑盒关键词",
            XIAOHEIHE_KIND: "已关注小黑盒热帖",
        }
        raise HTTPException(status.HTTP_409_CONFLICT, messages.get(payload["kind"], "已创建相同的小黑盒监控"))
    source = await shared_source(session, payload["platform"], payload["kind"], payload["target_value"])
    request_interval(source, interval)
    feed = CustomFeed(
        user_id=user.id,
        name=payload["name"],
        kind=payload["kind"],
        platform=payload["platform"] or _KIND_DEFAULT_PLATFORM[payload["kind"]],
        target_value=payload["target_value"],
        min_views=(payload["min_views"] or 0) if payload["kind"] in {"keyword", XIAOHEIHE_KEYWORD_KIND} else 0,
        push_enabled=bool(payload["push_enabled"]),
        is_public=bool(payload["is_public"]),
        source_id=source.id,
        interval_minutes=interval,
        filters=payload["filters"] or {},
    )
    session.add(feed)
    await session.commit()
    try:
        await queue.enqueue_custom_sweep()
    except Exception:  # noqa: BLE001 - 队列暂不可用不阻断入库，下轮轮询自动补采
        logger.debug("enqueue_custom_sweep skipped after create", exc_info=True)
    return {"id": feed.id, "name": feed.name}


@router.patch("/custom-feeds/{feed_id}")
async def update_custom_feed(
    feed_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    feed = await session.get(CustomFeed, feed_id)
    if feed is None or feed.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    payload = _validate_custom_payload(body, partial=True, default_kind=feed.kind)
    if ("kind" in body and payload["kind"] != feed.kind) or (
        "platform" in body and payload["platform"] != feed.platform
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控类型与平台创建后不可修改")
    from app.services.custom_policy import check_admission, shared_source, request_interval
    if payload["target_value"]:
        await _ensure_safe_custom_target(feed.kind, payload["target_value"])
        payload["target_value"] = await _resolve_custom_target(session, feed.kind, payload["target_value"])
    if payload["target_value"] or payload["interval_minutes"] is not None or payload["enabled"] is True:
        await check_admission(session, user.id, feed.platform, feed.kind,
            payload["target_value"] or feed.target_value, payload["interval_minutes"] or feed.interval_minutes, exclude_id=feed.id)
    if payload["name"]:
        feed.name = payload["name"]
    if payload["target_value"]:
        if payload["target_value"] != feed.target_value:
            # 已采集历史保留，新的目标从下一次调度开始生效。
            feed.target_value = payload["target_value"]
            feed.source_id = (await shared_source(session, feed.platform, feed.kind, feed.target_value)).id
            feed.last_collected_at = None
            feed.next_run_at = None
    if payload["min_views"] is not None and payload["min_views"] != feed.min_views:
        feed.min_views = payload["min_views"]
        feed.next_run_at = None
    if payload["push_enabled"] is not None:
        feed.push_enabled = bool(payload["push_enabled"])
    if payload["is_public"] is not None:
        feed.is_public = bool(payload["is_public"])
    if payload["enabled"] is not None:
        feed.enabled = bool(payload["enabled"])
        feed.next_run_at = None  # 重新启用立即调度
    if payload["filters"] is not None:
        feed.filters = payload["filters"]
        feed.next_run_at = None
    if payload["interval_minutes"] is not None:
        feed.interval_minutes = payload["interval_minutes"]
        feed.next_run_at = None
        if feed.source_id and feed.kind != XIAOHEIHE_KIND:
            from app.models import CustomFeedSource
            source = await session.get(CustomFeedSource, feed.source_id)
            if source and not source.last_error:
                source.next_run_at = None
    if feed.source_id and (payload["target_value"] or payload["enabled"] is True):
        from app.models import CustomFeedSource
        source = await session.get(CustomFeedSource, feed.source_id)
        request_interval(source, feed.interval_minutes)
    await session.commit()
    return {"id": feed.id, "updated": True, "target_value": feed.target_value}


@router.post("/custom-feeds/{feed_id}/collect", status_code=status.HTTP_202_ACCEPTED)
async def collect_my_custom_feed_now(
    feed_id: int,
    user: User = Depends(get_current_user),
    queue: BaseQueue = Depends(get_queue),
    session: AsyncSession = Depends(get_session),
):
    """用户手动触发一次定制监控采集（5 分钟冷却保护）。"""
    feed = await session.get(CustomFeed, feed_id)
    if feed is None or feed.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    if feed.status != "active" or not feed.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控处于暂停或停用状态，请先恢复后再抓取")
    now = utcnow()
    if feed.last_collected_at and (now - feed.last_collected_at) < timedelta(minutes=5):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "刚刚采集过，请至少间隔 5 分钟再手动刷新")
    feed.next_run_at = None
    if feed.source_id and feed.kind != XIAOHEIHE_KIND:
        from app.models import CustomFeedSource
        source = await session.get(CustomFeedSource, feed.source_id)
        if source and not source.last_error:
            source.next_run_at = None
    await session.commit()
    job_id = await queue.enqueue_custom_sweep()
    if job_id is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "任务队列暂时不可用")
    return {"id": feed.id, "status": "queued"}


@router.delete("/custom-feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_feed(
    feed_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    feed = await session.get(CustomFeed, feed_id)
    if feed is None or feed.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "监控不存在")
    await session.delete(feed)
    await session.commit()


# ---------- 个人专属 RSS 令牌 ----------


def _feed_token_view(token: str) -> dict:
    return {
        "token": token,
        "all_path": f"/api/rss/personal/{token}.xml",
        "keywords_path": f"/api/rss/personal/{token}.xml?mode=keywords",
        "revisions_path": f"/api/rss/personal/{token}.xml?mode=revisions",
        "mcp_path": f"/mcp?token={token}",
    }


@router.get("/feed-token")
async def get_feed_token(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """获取或首次签发个人专属 RSS 令牌。"""
    if not user.feed_token:
        user.feed_token = secrets.token_urlsafe(24)
        await session.commit()
    return _feed_token_view(user.feed_token)


@router.post("/feed-token/rotate")
async def rotate_feed_token(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """重置个人 RSS 令牌，旧订阅地址立即失效。"""
    user.feed_token = secrets.token_urlsafe(24)
    await session.commit()
    return _feed_token_view(user.feed_token)


# ---------- 通用用户 Webhook 推送 ----------


class WebhookCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    provider: str = Field(default="generic", max_length=24)
    url: str = Field(min_length=8, max_length=512)
    secret: str | None = Field(default=None, max_length=256)
    events_mode: str = Field(default="keywords", max_length=16)
    include_revisions: bool = False
    enabled: bool = True


class WebhookUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: str | None = Field(default=None, max_length=24)
    url: str | None = Field(default=None, min_length=8, max_length=512)
    secret: str | None = Field(default=None, max_length=256)
    clear_secret: bool = False
    events_mode: str | None = Field(default=None, max_length=16)
    include_revisions: bool | None = None
    enabled: bool | None = None


def _webhook_view(wh: UserWebhook) -> dict:
    return {
        "id": wh.id,
        "name": wh.name,
        "provider": wh.provider,
        "url": wh.url,
        "has_secret": bool(wh.secret),
        "enabled": wh.enabled,
        "events_mode": wh.events_mode,
        "include_revisions": bool(wh.include_revisions),
        "last_status": wh.last_status,
        "last_error": wh.last_error,
        "last_sent_at": wh.last_sent_at,
        "created_at": wh.created_at,
    }


@router.get("/webhooks")
async def list_webhooks(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.services.webhooks import MAX_WEBHOOKS

    rows = list(
        await session.scalars(
            select(UserWebhook)
            .where(UserWebhook.user_id == user.id)
            .order_by(UserWebhook.id.desc())
        )
    )
    return {
        "items": [_webhook_view(wh) for wh in rows],
        "max_webhooks": MAX_WEBHOOKS,
    }


@router.post("/webhooks", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.services.webhooks import (
        MAX_WEBHOOKS,
        WEBHOOK_MODES,
        WEBHOOK_PROVIDERS,
        WebhookError,
        validate_webhook_url_static,
    )

    if not user.is_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "请先完成邮箱验证后再添加 Webhook")
    provider = body.provider.strip().lower()
    if provider not in WEBHOOK_PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持的 Webhook 渠道类型")
    mode = body.events_mode.strip().lower()
    if mode not in WEBHOOK_MODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "推送范围仅支持 keywords、all 或 revisions")
    try:
        clean_url = validate_webhook_url_static(body.url)
    except WebhookError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    existing_count = (
        await session.scalar(
            select(func.count(UserWebhook.id)).where(UserWebhook.user_id == user.id)
        )
        or 0
    )
    if existing_count >= MAX_WEBHOOKS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"每个账号最多添加 {MAX_WEBHOOKS} 个 Webhook 通道"
        )

    include_rev = True if mode == "revisions" else bool(body.include_revisions)
    wh = UserWebhook(
        user_id=user.id,
        name=body.name.strip()[:64] or "我的 Webhook",
        provider=provider,
        url=clean_url,
        secret=(body.secret or "").strip() or None,
        enabled=bool(body.enabled),
        events_mode=mode,
        include_revisions=include_rev,
    )
    session.add(wh)
    # 首次接入 Webhook 时把游标设到当前最新修订，不回放历史事件。
    if mode in {"keywords", "all"} and user.webhook_notification_cursor is None:
        user.webhook_notification_cursor = (
            await session.scalar(
                select(EventRevision.id).order_by(EventRevision.id.desc()).limit(1)
            )
            or 0
        )
    if (mode == "revisions" or include_rev) and user.webhook_revision_cursor is None:
        user.webhook_revision_cursor = (
            await session.scalar(
                select(ContentVersion.id).order_by(ContentVersion.id.desc()).limit(1)
            )
            or 0
        )
    await session.commit()
    await session.refresh(wh)
    return _webhook_view(wh)


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    body: WebhookUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.services.webhooks import (
        WEBHOOK_MODES,
        WEBHOOK_PROVIDERS,
        WebhookError,
        validate_webhook_url_static,
    )

    wh = await session.get(UserWebhook, webhook_id)
    if wh is None or wh.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook 不存在")
    if body.name is not None:
        cleaned = body.name.strip()
        if not cleaned:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "名称不能为空")
        wh.name = cleaned[:64]
    if body.provider is not None:
        provider = body.provider.strip().lower()
        if provider not in WEBHOOK_PROVIDERS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不支持的 Webhook 渠道类型")
        wh.provider = provider
    if body.url is not None:
        try:
            wh.url = validate_webhook_url_static(body.url)
        except WebhookError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if body.clear_secret:
        wh.secret = None
    elif body.secret is not None and body.secret.strip():
        wh.secret = body.secret.strip()
    if body.events_mode is not None:
        mode = body.events_mode.strip().lower()
        if mode not in WEBHOOK_MODES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "推送范围仅支持 keywords、all 或 revisions")
        wh.events_mode = mode
        if mode == "revisions":
            wh.include_revisions = True
    if body.include_revisions is not None:
        wh.include_revisions = True if wh.events_mode == "revisions" else bool(body.include_revisions)
    if body.enabled is not None:
        wh.enabled = bool(body.enabled)
    if wh.enabled and wh.events_mode in {"keywords", "all"} and user.webhook_notification_cursor is None:
        user.webhook_notification_cursor = (
            await session.scalar(
                select(EventRevision.id).order_by(EventRevision.id.desc()).limit(1)
            )
            or 0
        )
    if wh.enabled and (wh.events_mode == "revisions" or wh.include_revisions) and user.webhook_revision_cursor is None:
        user.webhook_revision_cursor = (
            await session.scalar(
                select(ContentVersion.id).order_by(ContentVersion.id.desc()).limit(1)
            )
            or 0
        )
    await session.commit()
    await session.refresh(wh)
    return _webhook_view(wh)


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    wh = await session.get(UserWebhook, webhook_id)
    if wh is None or wh.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook 不存在")
    await session.delete(wh)
    await session.commit()


@router.post("/webhooks/{webhook_id}/test")
async def test_my_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.services.webhooks import WebhookError, test_webhook

    wh = await session.get(UserWebhook, webhook_id)
    if wh is None or wh.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook 不存在")
    try:
        return await test_webhook(session, wh)
    except WebhookError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
