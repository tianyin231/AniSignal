"""账号中心 API：关键词监控 CRUD、个性化关注流、账号概览。"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import literal, or_, select, func, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.clock import utcnow
from app.db import get_session
from app.api.admin import get_queue
from app.models import ContentVersion, NotificationDelivery, RawContent, Source, User, UserKeyword
from app.schemas.api import PublicContentItem, PublicContentList
from app.services.notifications import beijing_today, create_delivery, enabled_keywords
from app.services.queue import BaseQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", dependencies=[Depends(get_current_user)])

MAX_KEYWORDS = 10


def _title_condition(keyword: str):
    return ContentVersion.title.like(f"%{keyword}%")


async def _keyword_counts(
    session: AsyncSession, keywords: list[UserKeyword]
) -> dict[str, int]:
    """每个关键词当前命中的内容条数（单次 union_all 查询）。"""
    if not keywords:
        return {}
    queries = [
        select(
            literal(k.keyword).label("keyword"),
            func.count(func.distinct(RawContent.id)).label("cnt"),
        )
        .select_from(RawContent)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(_title_condition(k.keyword))
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
                select(func.count(func.distinct(RawContent.id)))
                .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
                .where(or_(*[_title_condition(k.keyword) for k in keywords if k.enabled]))
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
    user.notification_enabled = bool(body.get("enabled"))
    await session.commit()
    return {"enabled": user.notification_enabled, "schedule": "每天 08:00（北京时间）"}


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
    if not await enabled_keywords(session, user.id):
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
    counts = await _keyword_counts(session, keywords)
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

    conditions = [_title_condition(k.keyword) for k in keywords]
    joined = (
        select(func.count(func.distinct(RawContent.id)))
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .where(or_(*conditions))
    )
    total = await session.scalar(joined) or 0
    new_today = (
        await session.scalar(
            joined.where(RawContent.collected_at >= utcnow() - timedelta(hours=24))
        )
        or 0
    )

    rows = await session.execute(
        select(RawContent, Source, ContentVersion)
        .join(ContentVersion, ContentVersion.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(or_(*conditions))
        .order_by(RawContent.collected_at.desc(), RawContent.id.desc(), ContentVersion.version.desc())
        .limit(limit * 3)
        .offset(offset)
    )
    items: list[PublicContentItem] = []
    seen: set[int] = set()
    for raw, source, version in rows:
        if raw.id in seen or len(items) >= limit:
            continue
        seen.add(raw.id)
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
