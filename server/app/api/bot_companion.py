"""AstrBot 插件领取播报、回传结果及按真实会话身份操作订阅。"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bot import TargetBody, edit_target, require_tools
from app.api.deps import require_admin
from app.clock import utcnow
from app.db import get_session
from app.models import BotDelivery, BotReminder, BotTarget, RawContent
from app.services.bot_policy import reserve_delivery
from app.services.bot_briefs import format_detail, publication_label
from app.services.settings import get_setting, set_setting

router = APIRouter(prefix="/api/bot", dependencies=[Depends(require_tools)])
admin_router = APIRouter(prefix="/api/admin/bot", dependencies=[Depends(require_admin)])


class Actor(BaseModel):
    umo: str = Field(min_length=3, max_length=255)
    actor_id: str = Field(min_length=1, max_length=255)


async def actor_target(body, session):
    target = await session.scalar(select(BotTarget).where(BotTarget.umo == body.umo, BotTarget.enabled.is_(True)))
    if not target:
        raise HTTPException(403, "此会话未启用 AniSignal")
    return target


class SubscriptionBody(Actor):
    action: Literal["view", "keywords", "community", "daily", "updates"] = "view"
    value: str = Field(default="", max_length=1300)


@router.post("/subscription")
async def subscription(body: SubscriptionBody, session: AsyncSession = Depends(get_session)):
    target = await actor_target(body, session)
    if body.action != "view":
        if not target.config.get("chat_controls"):
            raise HTTPException(403, "此会话未开放聊天修改订阅")
        if ":GroupMessage:" in target.umo and body.actor_id not in target.config.get("manager_ids", []):
            raise HTTPException(403, "仅面板授权的群管理者可以修改全群订阅；发送 /情报会话 获取你的标识")
        config = dict(target.config)
        if body.action == "keywords":
            config["keywords"] = body.value.replace("，", ",").split(",")
        elif body.action == "community":
            from app.models.enums import COMMUNITY_LABELS
            config["community"] = next((k for k, v in COMMUNITY_LABELS.items() if v == body.value), body.value)
            if body.value == "全站":
                config["community"] = "all"
        elif body.action == "daily":
            config["daily_enabled"] = body.value != "off"
            if body.value != "off":
                config["daily_time"] = body.value
        else:
            if body.value not in ("on", "off"):
                raise HTTPException(422, "即时推送请使用 on 或 off")
            config["updates_enabled"] = body.value == "on"
        try:
            validated = TargetBody(name=target.name, umo=target.umo, enabled=target.enabled, **config)
        except ValueError:
            raise HTTPException(422, "设置无效，请核对分区、关键词长度或 HH:mm 时间")
        await edit_target(target.id, validated, session)
    return {"name": target.name, "community": target.config.get("community", "all"),
            "keywords": target.config.get("keywords", []), "daily_enabled": target.config.get("daily_enabled", True),
            "daily_time": target.config.get("daily_time", "08:00"), "updates_enabled": target.config.get("updates_enabled", False),
            "changed": body.action != "view"}


class ReminderBody(Actor):
    action: Literal["list", "create", "cancel"] = "list"
    text: str = Field(default="", max_length=500)
    due_at: datetime | None = None
    reminder_id: int | None = None
    request_id: str = Field(default="", max_length=255)


def reminder_view(row):
    return {"id": row.id, "target_id": row.target_id, "owner_id": row.owner_id, "body": row.body,
            "due_at": (row.due_at + timedelta(hours=8)).isoformat() + "+08:00", "status": row.status}


async def cancel_reminder(row, session):
    delivery = await session.scalar(select(BotDelivery).where(BotDelivery.dedupe_key == f"reminder:{row.id}"))
    if delivery:
        changed = await session.execute(update(BotDelivery).where(
            BotDelivery.id == delivery.id, BotDelivery.status.in_(["pending", "failed"]),
        ).values(status="cancelled", receipt=None))
        if not changed.rowcount:
            raise HTTPException(409, "提醒已进入发送或已发送，无法撤回")
    row.status = "cancelled"
    await session.commit()


@router.post("/reminders")
async def reminders(body: ReminderBody, session: AsyncSession = Depends(get_session)):
    target = await actor_target(body, session)
    if not target.config.get("reminders_enabled"):
        raise HTTPException(403, "此会话未开放定时提醒")
    if body.action == "create":
        if not body.text.strip() or not body.due_at or not body.request_id:
            raise HTTPException(422, "请提供提醒内容、明确时间和消息标识")
        due = body.due_at.astimezone(timezone.utc).replace(tzinfo=None) if body.due_at.tzinfo else body.due_at - timedelta(hours=8)
        key = hashlib.sha256(f"{target.id}:{body.actor_id}:{body.request_id}".encode()).hexdigest()
        existing = await session.scalar(select(BotReminder).where(BotReminder.request_key == key))
        if existing:
            return {"reminder": reminder_view(existing), "reused": True}
        if not utcnow() + timedelta(seconds=30) <= due <= utcnow() + timedelta(days=90):
            raise HTTPException(422, "提醒时间须在 30 秒后至 90 天内，默认北京时间")
        # 锁定会话后检查额度，避免同群并发创建绕过上限。
        await session.scalar(select(BotTarget).where(BotTarget.id == target.id).with_for_update())
        existing = await session.scalar(select(BotReminder).where(BotReminder.request_key == key).with_for_update())
        if existing:
            return {"reminder": reminder_view(existing), "reused": True}
        pending = list(await session.scalars(select(BotReminder).where(BotReminder.target_id == target.id, BotReminder.status == "pending").with_for_update()))
        if len(pending) >= 100 or sum(r.owner_id == body.actor_id for r in pending) >= 10:
            raise HTTPException(409, "待执行提醒已达上限：每人 10 条，每会话 100 条")
        row = BotReminder(target_id=target.id, owner_id=body.actor_id, request_key=key, body=body.text.strip(), due_at=due)
        session.add(row)
        await session.commit()
        return {"reminder": reminder_view(row), "notice": "将在当前会话公开发送，按分钟调度；停用会话期间不发送。"}
    if body.action == "cancel":
        row = await session.scalar(select(BotReminder).where(BotReminder.id == body.reminder_id,
            BotReminder.target_id == target.id, BotReminder.owner_id == body.actor_id).with_for_update())
        if not row:
            raise HTTPException(404, "未找到属于你的提醒")
        await cancel_reminder(row, session)
        return {"cancelled": row.id}
    rows = await session.scalars(select(BotReminder).where(BotReminder.target_id == target.id,
        BotReminder.owner_id == body.actor_id).order_by(BotReminder.id.desc()).limit(20))
    return {"reminders": [reminder_view(r) for r in rows]}


@router.post("/context")
async def conversation_context(body: Actor, session: AsyncSession = Depends(get_session)):
    target = await actor_target(body, session)
    rows = await session.scalars(select(BotDelivery).where(BotDelivery.target_id == target.id,
        BotDelivery.status == "accepted", BotDelivery.created_at >= utcnow() - timedelta(days=7))
        .order_by(BotDelivery.id.desc()).limit(3))
    return {"recent_notifications": [{"id": r.id, "kind": r.kind, "text": (r.rendered_body or r.body)[:4000],
            "brief": {"items": [{**{k: item.get(k) for k in ("title", "summary", "community", "published_at")},
                                 "published_label": publication_label(item)}
                                for item in r.brief["items"]]} if r.brief else None,
            "sent_at": (r.updated_at + timedelta(hours=8)).isoformat() + "+08:00"} for r in rows]}


class DetailBody(Actor):
    number: int = Field(default=1, ge=1, le=3)
    delivery_id: int | None = None


@router.post("/detail")
async def brief_detail(body: DetailBody, session: AsyncSession = Depends(get_session)):
    target = await actor_target(body, session)
    if not set(target.config.get("tools", [])).intersection({"digest", "news"}):
        raise HTTPException(403, "此会话未开放资讯查询")
    stmt = select(BotDelivery).where(BotDelivery.target_id == target.id, BotDelivery.status == "accepted",
                                    BotDelivery.created_at >= utcnow() - timedelta(days=7))
    if body.delivery_id is not None:
        stmt = stmt.where(BotDelivery.id == body.delivery_id)
    rows = await session.scalars(stmt.order_by(BotDelivery.id.desc()).limit(20))
    row = next((r for r in rows if r.brief and r.brief.get("items")), None)
    if not row or body.number > len(row.brief["items"]):
        raise HTTPException(404, "没找到这条编号，先发 /早报 看一下最新清单吧。")
    item = row.brief["items"][body.number - 1]
    ids = {i for s in item.get("sources", []) for i in s.get("content_ids", [])}
    if ids:
        visible = set(await session.scalars(select(RawContent.id).where(RawContent.id.in_(ids), RawContent.hidden.is_(False))))
        if visible != ids:
            raise HTTPException(404, "这条资料已更新或下架，请重新查询。")
    return {"text": format_detail(item), "item": item, "delivery_id": row.id,
            "grounding": "这是原播报的资料快照。时间以来源标注为准，不能当作刚刚发生的新消息。"}


class ClaimBody(BaseModel):
    version: str = Field(default="", max_length=30)


class ActivityBody(BaseModel):
    activities: dict[str, str] = Field(default_factory=dict, max_length=200)


@router.post("/activity")
async def report_activity(body: ActivityBody, session: AsyncSession = Depends(get_session)):
    """插件回传各会话最近活跃时间；仅运营参考数据，失败不影响主流程。"""
    known = set(await session.scalars(select(BotTarget.umo)))
    current = {umo: ts for umo, ts in (await get_setting(session, "bot_activity") or {}).items() if umo in known}
    for umo, value in body.activities.items():
        if umo not in known:
            continue
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            continue
        if ts.tzinfo:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        prev = current.get(umo)
        if not prev or ts > datetime.fromisoformat(prev):
            current[umo] = ts.isoformat()
    await set_setting(session, "bot_activity", current)
    return {"ok": True}


@router.post("/outbox/claim")
async def claim(body: ClaimBody, session: AsyncSession = Depends(get_session)):
    now = utcnow()
    await set_setting(session, "bot_runtime", {"last_seen": now.isoformat(), "version": body.version})
    rows = (await session.execute(select(BotDelivery, BotTarget).join(BotTarget, BotTarget.id == BotDelivery.target_id)
        .where(or_(BotDelivery.status == "pending", (BotDelivery.status == "failed") & (BotDelivery.updated_at <= now - timedelta(minutes=5))),
            BotDelivery.attempts < 3, BotTarget.config["delivery_mode"].as_string() == "astrbot",
            or_(BotTarget.enabled.is_(True), BotDelivery.kind == "manual"))
        .order_by(BotDelivery.id).limit(100))).all()
    for row, target in rows:
        if target.config.get("delivery_mode") != "astrbot" or (not target.enabled and row.kind != "manual"):
            continue
        if row.status == "failed" and row.updated_at > now - timedelta(minutes=5):
            continue
        max_images = 1
        receipt = secrets.token_hex(24)
        if await reserve_delivery(session, target, row, now=now, receipt=receipt):
            return {"delivery": {"id": row.id, "receipt": receipt, "umo": target.umo, "body": row.body,
                    "kind": row.kind, "images": (row.media or [])[:max_images], "provider_id": target.config.get("ai_provider_id", ""),
                    "brief": row.brief,
                    "instructions": target.config.get("ai_prompt", "")}}
    return {"delivery": None}


class ReceiptBody(BaseModel):
    receipt: str = Field(min_length=1, max_length=64)
    status: Literal["accepted", "failed", "unknown"]
    rendered_body: str = Field(default="", max_length=8000)
    error: str = Field(default="", max_length=255)


@router.post("/outbox/{delivery_id}/complete")
async def complete(delivery_id: int, body: ReceiptBody, session: AsyncSession = Depends(get_session)):
    row = await session.get(BotDelivery, delivery_id)
    if not row or not row.receipt or not secrets.compare_digest(row.receipt, body.receipt):
        raise HTTPException(409, "投递凭据已失效")
    if row.status == body.status:
        return {"ok": True}
    if row.status != "sending":
        raise HTTPException(409, "投递状态已改变，请人工核对")
    row.status, row.rendered_body = body.status, body.rendered_body or None
    row.error, row.updated_at = body.error or None, utcnow()
    await session.commit()
    return {"ok": True}


@admin_router.get("/companion")
async def companion(session: AsyncSession = Depends(get_session)):
    runtime = await get_setting(session, "bot_runtime") or {}
    last_seen = datetime.fromisoformat(runtime["last_seen"]) if runtime.get("last_seen") else None
    counts = dict((await session.execute(select(BotDelivery.status, func.count()).where(
        BotDelivery.created_at >= utcnow() - timedelta(days=1)).group_by(BotDelivery.status))).all())
    rows = (await session.execute(select(BotReminder, BotTarget.name).join(BotTarget, BotTarget.id == BotReminder.target_id)
        .order_by(BotReminder.id.desc()).limit(50))).all()
    return {"runtime": {**runtime, "online": bool(last_seen and last_seen > utcnow() - timedelta(minutes=2))},
            "counts": counts, "reminders": [{**reminder_view(r), "due_at": r.due_at, "name": name} for r, name in rows]}


@admin_router.post("/reminders/{reminder_id}/cancel")
async def admin_cancel(reminder_id: int, session: AsyncSession = Depends(get_session)):
    row = await session.scalar(select(BotReminder).where(BotReminder.id == reminder_id).with_for_update())
    if not row:
        raise HTTPException(404, "提醒不存在")
    await cancel_reminder(row, session)
    return {"ok": True}
