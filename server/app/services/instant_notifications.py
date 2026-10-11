"""按事件修订生成即时邮件；开启后生效，队列补偿只重取未发送任务。"""

import asyncio
from datetime import datetime, timedelta
from html import escape

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import get_settings
from app.models import Event, EventRevision, Evidence, NotificationDelivery, User
from app.services.email_templates import document
from app.services.mailer import send_email
from app.services.notifications import _plain, _safe_url, beijing_today, enabled_keywords, load_candidates
from app.services.settings import get_effective_smtp

_IMPORTANT_OFFICIAL = {"version_update", "presale", "balance", "shipping", "delay"}
_SUMMARY_NOTES = ("AI 摘要更新", "安全回退摘要更新")


def _important(event: Event) -> bool:
    return event.is_active and (
        event.importance or (event.identity == "official" and event.category in _IMPORTANT_OFFICIAL)
    )


def _business_revision(revision: EventRevision) -> bool:
    if revision.note.startswith((*_SUMMARY_NOTES, "人工锁定，", "解除锁定，")):
        return False
    if revision.note.startswith("人工编辑字段："):
        fields = {field.strip() for field in revision.note.split("：", 1)[1].split(",")}
        return bool(fields - {"summary", "manual_locked"})
    return True


async def _matching_items(session: AsyncSession, user: User, event: Event) -> list[dict]:
    keywords = await enabled_keywords(session, user.id)
    if not keywords:
        return []
    ids = list(await session.scalars(select(Evidence.raw_content_id).where(
        Evidence.event_id == event.id, Evidence.role != "excluded",
    )))
    items = await load_candidates(
        session, keywords, datetime(1970, 1, 1), utcnow() + timedelta(seconds=1),
        content_ids=ids, preferences=user.feed_preferences,
    )
    # 正文弱匹配须经过 AI 复核；即时通道只采纳标题和明确采集关注词。
    return [item for item in items if item["strength"] == "strong"][:5]


def _render(event: Event, revision: EventRevision, items: list[dict]) -> tuple[str, str, str]:
    title = _plain(event.title)
    keywords = sorted({keyword for item in items for keyword in item["keywords"]})
    subject = f"AniSignal 重要变化 · {title}"[:255]
    note = _plain(revision.note)
    detail_url = f"{get_settings().public_base_url.rstrip('/')}/events/{event.id}"
    lines = [title, f"命中关注词：{'、'.join(keywords)}", f"本次变化：{note}", "", "来源证据："]
    cards = []
    for item in items:
        source = _plain(item.get("source") or item["platform"])
        label = _plain(item["title"])
        url = _safe_url(item.get("url"))
        lines.extend([f"• {label}（{source}）", url or ""])
        link = f'<a href="{escape(url, quote=True)}">查看原文</a>' if url else ""
        cards.append(f"<li><strong>{escape(label)}</strong><br>{escape(source)} {link}</li>")
    lines.extend(["", f"查看事件：{detail_url}", "可在账号中心关闭重要变化邮件或调整关注词。"])
    html = document(
        title,
        f"<p>命中关注词：{escape('、'.join(keywords))}</p><p>本次变化：{escape(note)}</p>"
        f"<ul>{''.join(cards)}</ul><p><a href=\"{escape(detail_url, quote=True)}\">查看事件</a></p>"
        "<p>可在账号中心关闭重要变化邮件或调整关注词。</p>",
        eyebrow="重要变化",
    )
    return subject, "\n".join(lines), html


async def prepare_instant_notifications(session: AsyncSession) -> int:
    """修订进度和待发记录在同一事务提交，重启后可继续投递。"""
    user_ids = list(await session.scalars(select(User.id).where(
        User.notification_enabled.is_(True), User.instant_notification_enabled.is_(True),
        User.is_verified.is_(True), User.status == "active",
    )))
    created = 0
    for user_id in user_ids:
        # 偏好关闭/恢复与游标、待发记录一起串行，旧批次不能跨过新的开启基线。
        user = await session.get(User, user_id, populate_existing=True, with_for_update=True)
        if not user or not (user.notification_enabled and user.instant_notification_enabled and user.is_verified and user.status == "active"):
            await session.commit()
            continue
        if user.instant_notification_cursor is None:
            user.instant_notification_cursor = await session.scalar(select(func.max(EventRevision.id))) or 0
            await session.commit()
            continue
        revisions = list(await session.scalars(select(EventRevision).where(
            EventRevision.id > user.instant_notification_cursor,
        ).order_by(EventRevision.id).limit(200)))
        if not revisions:
            await session.commit()
            continue
        # 同一轮聚合的创建、增补证据等修订合并为一封，不为摘要改写发信。
        by_event = {row.event_id: row for row in revisions if _business_revision(row)}
        for event_id, revision in by_event.items():
            event = await session.get(Event, event_id)
            if not event or not _important(event):
                continue
            items = await _matching_items(session, user, event)
            if not items:
                continue
            key = f"instant:{user.id}:{event.id}:{revision.id}"
            if await session.scalar(select(NotificationDelivery.id).where(NotificationDelivery.dedupe_key == key)):
                continue
            subject, body, html = _render(event, revision, items)
            session.add(NotificationDelivery(
                user_id=user.id, kind="instant", source="schedule", period_date=beijing_today(),
                dedupe_key=key, status="pending", subject=subject, body=body, html_body=html,
                matched_keywords=sorted({keyword for item in items for keyword in item["keywords"]}),
                content_ids=[item["id"] for item in items],
            ))
            created += 1
        user.instant_notification_cursor = revisions[-1].id
        await session.commit()
    return created


async def send_instant_notification(session: AsyncSession, delivery_id: int) -> dict:
    delivery = await session.get(NotificationDelivery, delivery_id, populate_existing=True)
    if not delivery or delivery.kind != "instant" or delivery.status != "pending":
        return {"status": "skipped"}
    user = await session.get(User, delivery.user_id, populate_existing=True, with_for_update=True)
    eligible = user and user.status == "active" and user.is_verified and user.notification_enabled and user.instant_notification_enabled
    revision_id = int((delivery.dedupe_key or "").rsplit(":", 1)[-1])
    revision = await session.get(EventRevision, revision_id, populate_existing=True)
    event = await session.get(Event, revision.event_id, populate_existing=True) if revision else None
    items = await _matching_items(session, user, event) if eligible and event and _important(event) else []
    if not items:
        delivery.status = "skipped"
        delivery.error = "通知已关闭、事件不再重要或已不符合当前关注条件"
        await session.commit()
        return {"status": "skipped"}
    subject, body, html = _render(event, revision, items)
    # SMTP 结果不明时不自动重发，避免 worker 重启后重复投递。
    claimed = await session.execute(update(NotificationDelivery).where(
        NotificationDelivery.id == delivery.id, NotificationDelivery.status == "pending",
    ).values(status="sending"))
    await session.commit()
    if not claimed.rowcount:
        return {"status": "skipped"}
    try:
        sent = await asyncio.wait_for(send_email(
            user.email, subject, body, smtp=await get_effective_smtp(session), html_body=html,
        ), timeout=60)
        error = None if sent else "SMTP 发送失败，请查看服务端日志"
    except TimeoutError:
        sent, error = False, "SMTP 超时，发送结果未确认；未自动重发"
    except SQLAlchemyError:
        raise
    except Exception as exc:
        sent, error = False, f"邮件发送异常（{type(exc).__name__}），未自动重发"
    delivery.subject, delivery.body, delivery.html_body = subject, body, html
    delivery.matched_keywords = sorted({keyword for item in items for keyword in item["keywords"]})
    delivery.content_ids = [item["id"] for item in items]
    delivery.status = "sent" if sent else "failed"
    delivery.error = error
    delivery.sent_at = utcnow() if sent else None
    await session.commit()
    return {"status": delivery.status}


async def run_instant_notifications(session: AsyncSession) -> dict:
    created = await prepare_instant_notifications(session)
    # 中断中的 SMTP 投递不自动重试，留下可见结果供排查。
    await session.execute(update(NotificationDelivery).where(
        NotificationDelivery.kind == "instant", NotificationDelivery.status == "sending",
        NotificationDelivery.created_at < utcnow() - timedelta(minutes=30),
    ).values(status="failed", error="发送进程中断，结果未确认；未自动重发"))
    await session.commit()
    ids = list(await session.scalars(select(NotificationDelivery.id).where(
        NotificationDelivery.kind == "instant", NotificationDelivery.status == "pending",
    ).order_by(NotificationDelivery.id).limit(20)))
    stats = {"created": created, "sent": 0, "failed": 0, "skipped": 0}
    for delivery_id in ids:
        result = await send_instant_notification(session, delivery_id)
        stats[result["status"]] += 1
    return stats
