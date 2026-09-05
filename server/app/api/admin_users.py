"""Admin 用户管理：万级用户的快速查询、封禁/激活、订阅管理与邮件触达。

规模约定：用户表按 id 倒序分页（LIMIT/OFFSET），关键词数用一次分组子查询带出；
广播邮件按 50 人一块入队（worker 内逐条 SMTP），单次广播上限 2 万人。
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import get_queue
from app.api.deps import require_admin
from app.clock import utcnow
from app.db import get_session
from app.models import NotificationDelivery, User, UserKeyword
from app.services.auth import revoke_all_sessions
from app.services.email_templates import notice_email
from app.services.notifications import beijing_today
from app.services.queue import BaseQueue
from app.services.settings import get_effective_smtp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/users", dependencies=[Depends(require_admin)])

BROADCAST_CHUNK = 50
MAX_BROADCAST_TARGETS = 20_000
_EMAIL_SUBJECT_MAX = 200


async def _require_smtp(session: AsyncSession) -> None:
    smtp = await get_effective_smtp(session)
    if not smtp.get("host") or not smtp.get("user") or not smtp.get("password"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SMTP 尚未配置，请先在站点设置页完成邮箱服务配置",
        )


def _user_out(user: User, keyword_count: int | None = None) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar": user.avatar,
        "is_verified": user.is_verified,
        "notification_enabled": user.notification_enabled,
        "status": user.status,
        "banned_reason": user.banned_reason,
        "banned_at": user.banned_at,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "keyword_count": keyword_count,
    }


async def _get_user_or_404(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    return user


@router.get("")
async def list_users(
    q: str = Query(default="", max_length=120, description="按邮箱/昵称模糊搜索"),
    user_status: str = Query(default="", pattern="^(|active|banned)$", description="账号状态筛选"),
    verified: str = Query(default="", pattern="^(|yes|no)$", description="验证状态筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """用户分页快查：邮箱/昵称模糊匹配 + 状态筛选，万级规模走 id 倒序分页。"""
    conditions = []
    if q.strip():
        like = f"%{q.strip()}%"
        conditions.append(or_(User.email.like(like), User.display_name.like(like)))
    if user_status:
        conditions.append(User.status == user_status)
    if verified == "yes":
        conditions.append(User.is_verified.is_(True))
    elif verified == "no":
        conditions.append(User.is_verified.is_(False))

    keyword_count_sq = (
        select(UserKeyword.user_id, func.count().label("cnt"))
        .group_by(UserKeyword.user_id)
        .subquery()
    )
    base = select(User).outerjoin(keyword_count_sq, keyword_count_sq.c.user_id == User.id)
    count_base = select(func.count(User.id))
    if conditions:
        base = base.where(*conditions)
        count_base = count_base.where(*conditions)

    total = await session.scalar(count_base) or 0
    rows = await session.execute(
        select(User, func.coalesce(keyword_count_sq.c.cnt, 0))
        .outerjoin(keyword_count_sq, keyword_count_sq.c.user_id == User.id)
        .where(*conditions)
        .order_by(User.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = [_user_out(user, cnt) for user, cnt in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{user_id}")
async def user_detail(user_id: int, session: AsyncSession = Depends(get_session)):
    """用户详情：资料、关键词订阅与最近邮件投递记录。"""
    user = await _get_user_or_404(session, user_id)
    keywords = list(
        (
            await session.execute(
                select(UserKeyword)
                .where(UserKeyword.user_id == user_id)
                .order_by(UserKeyword.id.desc())
            )
        ).scalars()
    )
    deliveries = list(
        (
            await session.execute(
                select(NotificationDelivery)
                .where(NotificationDelivery.user_id == user_id)
                .order_by(NotificationDelivery.id.desc())
                .limit(10)
            )
        ).scalars()
    )
    sent_total = await session.scalar(
        select(func.count(NotificationDelivery.id)).where(
            NotificationDelivery.user_id == user_id,
            NotificationDelivery.status == "sent",
        )
    ) or 0
    return {
        **_user_out(user, len(keywords)),
        "keywords": [
            {"id": k.id, "keyword": k.keyword, "enabled": k.enabled, "created_at": k.created_at}
            for k in keywords
        ],
        "sent_total": sent_total,
        "recent_deliveries": [
            {
                "id": d.id,
                "kind": d.kind,
                "status": d.status,
                "subject": d.subject,
                "error": d.error,
                "created_at": d.created_at,
                "sent_at": d.sent_at,
            }
            for d in deliveries
        ],
    }


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """账号管理：封禁/解封（附原因）、邮件通知订阅开关。封禁即时吊销全部会话。"""
    user = await _get_user_or_404(session, user_id)
    actions: list[str] = []

    if "banned" in body:
        banned = bool(body["banned"])
        if banned and user.status != "banned":
            reason = str(body.get("ban_reason") or "").strip()[:500]
            user.status = "banned"
            user.banned_reason = reason or None
            user.banned_at = utcnow()
            await revoke_all_sessions(session, user.id)
            actions.append("banned")
        elif not banned and user.status == "banned":
            user.status = "active"
            user.banned_reason = None
            user.banned_at = None
            actions.append("unbanned")

    if "notification_enabled" in body:
        user.notification_enabled = bool(body["notification_enabled"])
        actions.append("notification_updated")

    await session.commit()
    logger.info("admin updated user %s: %s", user_id, actions or "no-op")
    return {**_user_out(user), "actions": actions}


@router.post("/{user_id}/email", status_code=status.HTTP_202_ACCEPTED)
async def email_user(
    user_id: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
    queue: BaseQueue = Depends(get_queue),
):
    """给单个用户发送邮件（违规提醒、优惠信息等），走队列异步投递并留档。"""
    subject = str(body.get("subject") or "").strip()
    text = str(body.get("body") or "").strip()
    if not subject or len(subject) > _EMAIL_SUBJECT_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮件主题不能为空且不超过 200 字")
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮件正文不能为空")
    user = await _get_user_or_404(session, user_id)
    if user.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "已封禁账号不发送邮件")
    await _require_smtp(session)

    delivery = NotificationDelivery(
        user_id=user.id,
        kind="admin",
        source="admin",
        period_date=beijing_today(),
        dedupe_key=f"admin:{uuid.uuid4().hex}:{user.id}",
        status="pending",
        subject=subject[:255],
        body=text,
        html_body=notice_email(subject, text),
    )
    session.add(delivery)
    await session.commit()
    job_id = await queue.enqueue_admin_email([delivery.id])
    if job_id is None:
        delivery.status = "failed"
        delivery.error = "任务队列暂时不可用"
        await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "任务队列暂时不可用")
    return {"delivery_id": delivery.id, "status": "pending"}


@router.post("/broadcast", status_code=status.HTTP_202_ACCEPTED)
async def broadcast_email(
    body: dict,
    session: AsyncSession = Depends(get_session),
    queue: BaseQueue = Depends(get_queue),
):
    """广播邮件：默认发给全部已验证的活跃用户，按 50 人一块入队分批投递。"""
    subject = str(body.get("subject") or "").strip()
    text = str(body.get("body") or "").strip()
    if not subject or len(subject) > _EMAIL_SUBJECT_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮件主题不能为空且不超过 200 字")
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮件正文不能为空")
    if not bool(body.get("confirm")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 confirm 确认位")
    verified_only = bool(body.get("verified_only", True))
    await _require_smtp(session)

    conditions = [User.status == "active"]
    if verified_only:
        conditions.append(User.is_verified.is_(True))
    target_ids = list(
        (
            await session.execute(
                select(User.id).where(*conditions).order_by(User.id).limit(MAX_BROADCAST_TARGETS)
            )
        ).scalars()
    )
    if not target_ids:
        return {"targets": 0, "queued": 0, "batches": 0}

    batch = uuid.uuid4().hex[:12]
    html_body = notice_email(subject, text)
    queued = 0
    batches = 0
    for start in range(0, len(target_ids), BROADCAST_CHUNK):
        chunk = target_ids[start : start + BROADCAST_CHUNK]
        deliveries = [
            NotificationDelivery(
                user_id=user_id,
                kind="broadcast",
                source="admin",
                period_date=beijing_today(),
                dedupe_key=f"broadcast:{batch}:{user_id}",
                status="pending",
                subject=subject[:255],
                body=text,
                html_body=html_body,
            )
            for user_id in chunk
        ]
        session.add_all(deliveries)
        await session.commit()
        delivery_ids = [d.id for d in deliveries]
        if await queue.enqueue_admin_email(delivery_ids) is not None:
            queued += len(delivery_ids)
        batches += 1
    if queued < len(target_ids):
        logger.warning("broadcast %s: %s deliveries failed to enqueue", batch, len(target_ids) - queued)
    return {"targets": len(target_ids), "queued": queued, "batches": batches, "batch": batch}


@router.get("/deliveries/recent")
async def recent_admin_deliveries(
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """管理端邮件投递记录（单发 + 广播），供触达审计。"""
    rows = await session.execute(
        select(NotificationDelivery, User.email)
        .join(User, User.id == NotificationDelivery.user_id)
        .where(NotificationDelivery.kind.in_(("admin", "broadcast")))
        .order_by(NotificationDelivery.id.desc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": d.id,
                "user_id": d.user_id,
                "email": email,
                "kind": d.kind,
                "status": d.status,
                "subject": d.subject,
                "error": d.error,
                "created_at": d.created_at,
                "sent_at": d.sent_at,
            }
            for d, email in rows
        ]
    }
