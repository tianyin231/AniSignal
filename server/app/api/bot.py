"""管理员通知面板，以及 AstrBot 专用的情报查询接口。"""

import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.clock import utcnow
from app.communities import community_query_pattern
from app.db import get_session
from app.models import BotDelivery, BotReminder, BotTarget
from app.services import bot
from app.services.settings import get_setting, set_setting

router = APIRouter(prefix="/api/admin/bot", dependencies=[Depends(require_admin)])
tools_router = APIRouter(prefix="/api/bot")


class TargetBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    umo: str = Field(min_length=3, max_length=255, pattern=r"^[^:]+:(FriendMessage|GroupMessage):[^:]+$")
    enabled: bool = False
    community: str = Field(default="all", pattern=community_query_pattern())
    keywords: list[str] = Field(default_factory=list, max_length=20)
    daily_enabled: bool = True
    daily_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    updates_enabled: bool = False
    quiet_start: int = Field(default=23, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    quiet_critical: bool = False
    merge_minutes: int = Field(default=30, ge=0, le=60)
    hourly_limit: int = Field(default=2, ge=0, le=50)
    min_push_interval_minutes: int = Field(default=20, ge=0, le=120)
    calendar_enabled: bool = True
    calendar_time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_style: Literal["compact", "split"] = "compact"
    max_images: int = Field(default=1, ge=1, le=9)
    tools: list[Literal["digest", "news", "products", "trending", "calendar", "codes"]] = Field(default_factory=lambda: list(bot.TOOLS))
    ai_prompt: str = Field(default="简洁、自然地回答，保留来源与时间。", max_length=2000)
    ai_provider_id: str = Field(default="", max_length=200)
    delivery_mode: Literal["direct", "astrbot"] = "direct"
    chat_controls: bool = False
    reminders_enabled: bool = False
    activities_enabled: bool = False
    manager_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("manager_ids")
    @classmethod
    def clean_managers(cls, values):
        if any(len(v) > 255 for v in values):
            raise ValueError("用户标识过长")
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, values):
        if any(len(v) > 64 for v in values):
            raise ValueError("每个关键词不超过 64 字")
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))


class BridgeBody(BaseModel):
    endpoint: str = Field(max_length=500)
    api_key: str = Field(default="", max_length=500)

    @field_validator("endpoint")
    @classmethod
    def check_endpoint(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("填写不带密钥参数的 HTTP 地址，密钥放入单独输入框")
        return value.rstrip("/")


class QueryBody(BaseModel):
    tool: Literal["digest", "news", "products", "trending", "calendar", "codes"] = "digest"
    query: str = Field(default="", max_length=100)


class ToolQuery(QueryBody):
    umo: str = Field(max_length=255)


class MessageBody(BaseModel):
    content: str = Field(default="", max_length=3500)


def target_view(row):
    return {**TargetBody(name=row.name, umo=row.umo, enabled=row.enabled, **row.config).model_dump(),
            "id": row.id, "created_at": row.created_at}


async def get_target(target_id, session):
    row = await session.get(BotTarget, target_id)
    if not row:
        raise HTTPException(404, "目标不存在")
    return row


@router.get("")
async def overview(session: AsyncSession = Depends(get_session)):
    bridge = await get_setting(session, "bot_bridge") or {}
    return {"bridge": {"endpoint": bridge.get("endpoint", ""), "api_key_set": bool(bridge.get("api_key")), "tools_key_set": bool(bridge.get("tools_key"))},
            "targets": [target_view(t) for t in await session.scalars(select(BotTarget).order_by(BotTarget.id))], "tools": bot.TOOLS,
            "activity": await get_setting(session, "bot_activity") or {}}


@router.put("/bridge")
async def save_bridge(body: BridgeBody, session: AsyncSession = Depends(get_session)):
    current = await get_setting(session, "bot_bridge") or {}
    current.update(endpoint=body.endpoint, api_key=body.api_key or current.get("api_key", ""))
    current.setdefault("tools_key", secrets.token_urlsafe(32))
    await set_setting(session, "bot_bridge", current)
    return {"ok": True}


@router.post("/targets")
async def add_target(body: TargetBody, session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(BotTarget.id).where(BotTarget.umo == body.umo)):
        raise HTTPException(409, "该会话已存在，请编辑已有目标")
    row = BotTarget(name=body.name, umo=body.umo, enabled=body.enabled,
                    config=body.model_dump(exclude={"name", "umo", "enabled"}))
    session.add(row)
    await session.commit()
    return target_view(row)


@router.put("/targets/{target_id}")
async def edit_target(target_id: int, body: TargetBody, session: AsyncSession = Depends(get_session)):
    row = await get_target(target_id, session)
    if body.umo != row.umo:
        raise HTTPException(409, "变更收件人请新建会话，避免旧投递被重发给其他人")
    if await session.scalar(select(BotTarget.id).where(BotTarget.umo == body.umo, BotTarget.id != target_id)):
        raise HTTPException(409, "该会话已存在")
    config = body.model_dump(exclude={"name", "umo", "enabled"})
    # 重新启用或修改订阅范围时从现在开始，避免补发停用期间的旧消息。
    if body.enabled and (not row.enabled or config != row.config or body.umo != row.umo):
        row.last_scan_at = utcnow()
    if body.umo != row.umo or config != row.config or not body.enabled:
        await session.execute(delete(BotDelivery).where(BotDelivery.target_id == target_id, BotDelivery.status == "pending", BotDelivery.kind != "reminder"))
        # 已失败的旧范围消息保留审计，但不再自动重试。
        from sqlalchemy import update
        await session.execute(update(BotDelivery).where(BotDelivery.target_id == target_id, BotDelivery.status == "failed", BotDelivery.kind != "reminder").values(attempts=3))
    row.name, row.umo, row.enabled, row.config = body.name, body.umo, body.enabled, config
    await session.commit()
    return target_view(row)


@router.delete("/targets/{target_id}")
async def remove_target(target_id: int, session: AsyncSession = Depends(get_session)):
    await get_target(target_id, session)
    await session.execute(delete(BotDelivery).where(BotDelivery.target_id == target_id))
    await session.execute(delete(BotReminder).where(BotReminder.target_id == target_id))
    await session.execute(delete(BotTarget).where(BotTarget.id == target_id))
    await session.commit()
    return {"ok": True}


@router.post("/targets/{target_id}/preview")
async def preview(target_id: int, body: QueryBody, session: AsyncSession = Depends(get_session)):
    return await bot.query_target(session, await get_target(target_id, session), body.tool, body.query)


@router.post("/targets/{target_id}/send")
async def send(target_id: int, body: MessageBody, session: AsyncSession = Depends(get_session)):
    target = await get_target(target_id, session)
    payload = {} if body.content.strip() else await bot.query_target(session, target)
    text = body.content.strip() or payload["text"]
    row = await bot.create_delivery(session, target, "manual", text, f"manual:{secrets.token_hex(16)}", brief=payload.get("brief"))
    await bot.deliver(session, target, row, force=True)
    return {"id": row.id, "status": row.status, "error": row.error}


@router.get("/deliveries")
async def deliveries(target_id: int | None = None, session: AsyncSession = Depends(get_session)):
    stmt = select(BotDelivery, BotTarget.name).join(BotTarget, BotTarget.id == BotDelivery.target_id)
    if target_id:
        stmt = stmt.where(BotDelivery.target_id == target_id)
    rows = (await session.execute(stmt.order_by(BotDelivery.id.desc()).limit(50))).all()
    return [{"id": r.id, "target_id": r.target_id, "name": name, "kind": r.kind, "body": r.body, "rendered_body": r.rendered_body,
             "status": r.status, "error": r.error, "attempts": r.attempts, "created_at": r.created_at} for r, name in rows]


@router.post("/deliveries/{delivery_id}/retry")
async def retry(delivery_id: int, session: AsyncSession = Depends(get_session)):
    row = await session.get(BotDelivery, delivery_id)
    if not row or row.status not in ("failed", "unknown"):
        raise HTTPException(409, "仅失败或结果未知的记录可以重试")
    row.status, row.attempts, row.receipt = "pending", 0, None
    await session.commit()
    await bot.deliver(session, await get_target(row.target_id, session), row, force=True)
    return {"status": row.status, "error": row.error}


async def require_tools(x_bot_key: str = Header(default=""), session: AsyncSession = Depends(get_session)):
    expected = (await get_setting(session, "bot_bridge") or {}).get("tools_key", "")
    if not expected or not secrets.compare_digest(x_bot_key, expected):
        raise HTTPException(401, "工具鉴权失败")


@tools_router.get("/targets", dependencies=[Depends(require_tools)])
async def tool_targets(session: AsyncSession = Depends(get_session)):
    # 返回全部会话（含已暂停）：插件在聊天里回显注册状态需要名称与启停标记，
    # 工具可用性由插件按 enabled 自行门禁。
    return [{"umo": t.umo, "name": t.name, "enabled": t.enabled,
             "tools": t.config.get("tools", []), "instructions": t.config.get("ai_prompt", ""),
             "provider_id": t.config.get("ai_provider_id", ""),
             "chat_controls": t.config.get("chat_controls", False), "reminders_enabled": t.config.get("reminders_enabled", False),
             "activities_enabled": t.config.get("activities_enabled", False)}
            for t in await session.scalars(select(BotTarget).order_by(BotTarget.id))]


@tools_router.post("/query", dependencies=[Depends(require_tools)])
async def tool_query(body: ToolQuery, session: AsyncSession = Depends(get_session)):
    target = await session.scalar(select(BotTarget).where(BotTarget.umo == body.umo, BotTarget.enabled.is_(True)))
    if not target or body.tool not in target.config.get("tools", []):
        raise HTTPException(403, "此会话尚未启用该查询工具")
    return await bot.query_target(session, target, body.tool, body.query)
