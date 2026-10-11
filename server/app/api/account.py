"""账号中心 API：关键词监控 CRUD、个性化关注流、账号概览。"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import literal, or_, select, func, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.clock import utcnow
from app.communities import COMMUNITIES, DEFAULT_COMMUNITY
from app.db import get_session
from app.api.admin import get_queue
from app.models import (
    ContentVersion,
    CustomFeed,
    EventRevision,
    NotificationDelivery,
    RawContent,
    Source,
    User,
    UserKeyword,
)
from app.schemas.api import PublicContentItem, PublicContentList
from app.services.notifications import beijing_today, create_delivery, enabled_keywords
from app.services.feed_preferences import preference_conditions
from app.services.queue import BaseQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", dependencies=[Depends(get_current_user)])

MAX_KEYWORDS = 10


def _title_condition(keyword: str):
    return func.lower(ContentVersion.title).contains(keyword.lower(), autoescape=True)


def _latest_versions():
    return (
        select(ContentVersion.raw_content_id.label("rid"), func.max(ContentVersion.version).label("version"))
        .group_by(ContentVersion.raw_content_id)
        .subquery()
    )


def _join_latest(query, latest):
    return query.select_from(RawContent).join(latest, latest.c.rid == RawContent.id).join(
        ContentVersion,
        (ContentVersion.raw_content_id == latest.c.rid) & (ContentVersion.version == latest.c.version),
    )


async def _keyword_counts(
    session: AsyncSession, keywords: list[UserKeyword], preferences: dict | None = None
) -> dict[str, int]:
    """每个关键词当前命中的内容条数（单次 union_all 查询）。"""
    if not keywords:
        return {}
    latest = _latest_versions()
    queries = [
        _join_latest(select(
            literal(k.keyword).label("keyword"),
            func.count(RawContent.id).label("cnt"),
        ), latest)
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
                _join_latest(select(func.count(RawContent.id)), _latest_versions())
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
    # 偏好改变后，手动早报不能复用旧筛选条件生成的快照。
    user.feed_preferences = {**preferences, "updated_at": utcnow().isoformat()}
    await session.commit()
    return preferences


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
    for field in ("enabled", "instant_enabled"):
        if field in body and not isinstance(body[field], bool):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "通知开关必须是布尔值")
    if body.get("instant_enabled"):
        if not user.is_verified:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先验证邮箱")
        if not await enabled_keywords(session, user.id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先启用至少一个关键词")
    if "enabled" in body:
        user.notification_enabled = body["enabled"]
    if "instant_enabled" in body:
        user.instant_notification_enabled = body["instant_enabled"]
    if user.notification_enabled and user.instant_notification_enabled and not was_active:
        user.instant_notification_cursor = await session.scalar(select(func.max(EventRevision.id))) or 0
    if not user.notification_enabled or not user.instant_notification_enabled:
        await session.execute(update(NotificationDelivery).where(
            NotificationDelivery.user_id == user.id, NotificationDelivery.kind == "instant",
            NotificationDelivery.status == "pending",
        ).values(status="skipped", error="用户已关闭即时邮件通知"))
    await session.commit()
    return {"enabled": user.notification_enabled, "instant_enabled": user.instant_notification_enabled, "schedule": "每天 08:00（北京时间）"}


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
    latest = _latest_versions()
    joined = _join_latest(select(func.count(RawContent.id)), latest).where(*conditions)
    total = await session.scalar(joined) or 0
    new_today = (
        await session.scalar(
            joined.where(RawContent.collected_at >= utcnow() - timedelta(hours=24))
        )
        or 0
    )

    rows = await session.execute(
        _join_latest(select(RawContent, Source, ContentVersion), latest)
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


# ---------- 定制监控（用户自建 UID/关键词抓取源） ----------

MAX_CUSTOM_FEEDS = 10


def _validate_custom_payload(body: dict, *, partial: bool = False) -> dict:
    kind = str(body.get("kind") or "").strip()
    if kind and kind not in {"uid", "keyword"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控类型只支持 uid / keyword")
    platform = str(body.get("platform") or "bilibili").strip()
    if platform and platform not in {"bilibili"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "定制平台当前仅支持 B 站")
    name = str(body.get("name") or "").strip()
    if ("name" in body or not partial) and not 1 <= len(name) <= 64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控名称需为 1 至 64 字")
    target = str(body.get("target_value") or "").strip()
    if ("target_value" in body or not partial) and not 1 <= len(target) <= 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控目标需为 1 至 128 字")
    if kind == "uid" and target and not target.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "UID 只能是数字")
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
    return {
        "name": name,
        "kind": kind,
        "platform": platform,
        "target_value": target,
        "min_views": min_views,
        "push_enabled": body.get("push_enabled"),
        "is_public": body.get("is_public"),
        "enabled": body.get("enabled"),
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
            "created_at": f.created_at,
        }
        for f in feeds
    ]


@router.post("/custom-feeds", status_code=status.HTTP_201_CREATED)
async def create_custom_feed(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """新建定制监控；免费额度 10 个。"""
    payload = _validate_custom_payload(body)
    if not payload["name"] or not payload["kind"] or not payload["target_value"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "名称、监控类型与目标均不能为空")
    count = await session.scalar(
        select(func.count(CustomFeed.id)).where(CustomFeed.user_id == user.id)
    )
    if (count or 0) >= MAX_CUSTOM_FEEDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"最多 {MAX_CUSTOM_FEEDS} 个定制监控")
    feed = CustomFeed(
        user_id=user.id,
        name=payload["name"],
        kind=payload["kind"],
        platform=payload["platform"] or "bilibili",
        target_value=payload["target_value"],
        min_views=payload["min_views"] or 0,
        push_enabled=bool(payload["push_enabled"]),
        is_public=bool(payload["is_public"]),
    )
    session.add(feed)
    await session.commit()
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
    payload = _validate_custom_payload(body, partial=True)
    if ("kind" in body and payload["kind"] != feed.kind) or (
        "platform" in body and payload["platform"] != feed.platform
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "监控类型与平台创建后不可修改")
    if payload["name"]:
        feed.name = payload["name"]
    if payload["target_value"]:
        if feed.kind == "uid" and not payload["target_value"].isdigit():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "UID 只能是数字")
        if payload["target_value"] != feed.target_value:
            # 已采集历史保留，新的目标从下一次调度开始生效。
            feed.target_value = payload["target_value"]
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
    await session.commit()
    return {"id": feed.id, "updated": True}


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
