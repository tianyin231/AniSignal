"""管理员通知面板，以及 AstrBot 专用的情报查询接口。"""

import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.clock import utcnow
from app.communities import COMMUNITIES, community_query_pattern
from app.custom_presets import CUSTOM_PRESETS, promote_subscriptions
from app.db import get_session
from app.models import BotClient, BotDelivery, BotReminder, BotTarget, SiteSetting
from app.services import bot
from app.services import bot_clients
from app.services import bot_preferences
from app.services.bot_access import actor_error, target_error
from app.services.bot_channels import channel_options, selected_communities
from app.services.settings import get_setting, set_setting

router = APIRouter(prefix="/api/admin/bot", dependencies=[Depends(require_admin)])
tools_router = APIRouter(prefix="/api/bot")


class TargetBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    umo: str = Field(min_length=3, max_length=255, pattern=r"^[^:]+:(FriendMessage|GroupMessage):[^:]+$")
    enabled: bool = False
    community: str = Field(default="all", pattern=community_query_pattern())
    communities: list[str] | None = Field(default=None, max_length=len(COMMUNITIES))
    channels: list[str] = Field(default_factory=list, max_length=200)
    scope_mode: Literal["legacy", "channels", "custom"] = "legacy"
    inherit_preferences: bool = False
    inherit_keywords: bool = False
    custom_feeds: list[str] = Field(default_factory=list, max_length=30)
    custom_feed_ids: list[int] = Field(default_factory=list, max_length=100)
    daily_sources: list[str] = Field(default_factory=list, max_length=200)
    updates_sources: list[str] = Field(default_factory=list, max_length=200)
    calendar_kinds: list[Literal["activity", "pool", "maintenance", "deadline"]] = Field(default_factory=lambda: ["activity", "pool", "maintenance", "deadline"])
    keywords: list[str] = Field(default_factory=list, max_length=20)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=20)
    daily_enabled: bool = True
    daily_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    updates_enabled: bool = False
    quiet_start: int = Field(default=23, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    quiet_critical: bool = False
    merge_minutes: int = Field(default=0, ge=0, le=60)
    hourly_limit: int = Field(default=2, ge=0, le=50)
    min_push_interval_minutes: int = Field(default=20, ge=0, le=120)
    calendar_enabled: bool = True
    calendar_time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_style: Literal["compact", "split"] = "compact"
    max_images: int = Field(default=1, ge=1, le=9)
    tools: list[Literal["digest", "news", "products", "trending", "calendar", "codes"]] = Field(default_factory=lambda: list(bot.TOOLS))
    delivery_mode: Literal["direct", "astrbot"] = "direct"
    chat_controls: bool = False
    reminders_enabled: bool = False
    activities_enabled: bool = False
    manager_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("communities")
    @classmethod
    def clean_communities(cls, values):
        if values is not None and (not values or any(value not in COMMUNITIES for value in values)):
            raise ValueError("至少选择一个有效分区")
        return list(dict.fromkeys(values)) if values is not None else None

    @field_validator("channels")
    @classmethod
    def clean_channels(cls, values):
        if any(value not in channel_options() for value in values):
            raise ValueError("通知频道无效")
        return list(dict.fromkeys(values))

    @field_validator("custom_feeds")
    @classmethod
    def clean_custom_feeds(cls, values):
        if any(value not in CUSTOM_PRESETS for value in values):
            raise ValueError("定制信息源无效")
        return list(dict.fromkeys(values))

    @field_validator("custom_feed_ids")
    @classmethod
    def clean_feed_ids(cls, values):
        if any(value <= 0 for value in values):
            raise ValueError("定制监控标识无效")
        return list(dict.fromkeys(values))

    @field_validator("daily_sources", "updates_sources")
    @classmethod
    def clean_sources(cls, values):
        options = set(channel_options()) | {f"preset:{key}" for key in CUSTOM_PRESETS}
        if any(value not in options and not (value.startswith("feed:") and value[5:].isascii() and value[5:].isdigit() and 0 < int(value[5:]) < 2**63) for value in values):
            raise ValueError("通知来源无效")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def sync_legacy_community(self):
        promoted = promote_subscriptions(self.model_dump())
        for field in ("custom_feeds", "channels", "communities", "community", "scope_mode", "daily_sources", "updates_sources"):
            setattr(self, field, promoted[field])
        if self.channels:
            self.communities = selected_communities(self.channels)
            if self.scope_mode == "legacy":
                self.scope_mode = "channels"
        if self.communities is not None:
            self.community = self.communities[0] if len(self.communities) == 1 else "all"
        return self

    @field_validator("manager_ids")
    @classmethod
    def clean_managers(cls, values):
        if any(len(v) > 255 for v in values):
            raise ValueError("用户标识过长")
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))

    @field_validator("keywords", "exclude_keywords")
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
    actor_id: str = Field(default="", max_length=255)


class MessageBody(BaseModel):
    content: str = Field(default="", max_length=3500)


class ClientBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class ConfigurationBody(BaseModel):
    targets: list[TargetBody] = Field(default_factory=list, max_length=200)
    version: str = Field(default="", max_length=30)

    @model_validator(mode="after")
    def unique_sessions(self):
        if len({target.umo for target in self.targets}) != len(self.targets):
            raise ValueError("同一插件内不能重复配置会话标识")
        return self


def target_view(row):
    config = {**row.config, "communities": bot.subscribed_communities(row) or row.config.get("communities")}
    return {**TargetBody(name=row.name, umo=row.umo, enabled=row.enabled, **config).model_dump(),
            "id": row.id, "created_at": row.created_at, "client_id": row.client_id,
            "managed_by_plugin": row.managed_by_plugin, "plugin_configured": row.config.get("_plugin_configured", True)}


async def get_target(target_id, session):
    row = await session.get(BotTarget, target_id)
    if not row:
        raise HTTPException(404, "目标不存在")
    return row


@router.get("")
async def overview(session: AsyncSession = Depends(get_session)):
    bridge = await get_setting(session, "bot_bridge") or {}
    rows = list(await session.scalars(select(BotTarget).order_by(BotTarget.id)))
    clients = await bot_clients.client_views(session)
    client_names = {client["id"]: client["name"] for client in clients}
    activity = {client_id: await get_setting(session, bot_clients.setting_key("bot_activity", client_id)) or {}
                for client_id in {row.client_id for row in rows}}
    return {"bridge": {"endpoint": bridge.get("endpoint", ""), "api_key_set": bool(bridge.get("api_key")), "tools_key_set": bool(bridge.get("tools_key"))},
            "targets": [{**target_view(t), "client_name": client_names.get(t.client_id, "旧直发通道"),
                         "last_activity": activity[t.client_id].get(t.umo)} for t in rows],
            "clients": clients, "tools": bot.TOOLS, "channels": channel_options(),
            "activity": activity.get(bot_clients.LEGACY_CLIENT, {})}


@router.post("/clients")
async def add_client(body: ClientBody, session: AsyncSession = Depends(get_session)):
    client_id, key = await bot_clients.issue_key(session, body.name)
    if not body.enabled:
        await edit_client(client_id, body, session)
    return {"id": client_id, "key": key}


@router.put("/clients/{client_id}")
async def edit_client(client_id: str, body: ClientBody, session: AsyncSession = Depends(get_session)):
    views = {row["id"]: row for row in await bot_clients.client_views(session)}
    if client_id not in views:
        raise HTTPException(404, "插件接入不存在")
    user_client = await session.get(BotClient, client_id)
    if user_client:
        if body.enabled:
            raise HTTPException(409, "用户接入由所属账号管理，请在账号中心创建或重置 Token")
        await bot_clients.revoke_user_client(session, user_client)
        return {"ok": True}
    clients = dict(await get_setting(session, "bot_clients") or {})
    clients[client_id] = {**clients.get(client_id, {}), "name": body.name, "enabled": body.enabled}
    if not body.enabled:
        owned = select(BotTarget.id).where(BotTarget.client_id == client_id,
            or_(BotTarget.managed_by_plugin.is_(True), BotTarget.config["delivery_mode"].as_string() == "astrbot"))
        await session.execute(update(BotDelivery).where(BotDelivery.target_id.in_(owned),
            BotDelivery.status.in_(["pending", "failed"]), BotDelivery.kind != "reminder")
            .values(status="cancelled", receipt=None, error="插件接入已停用"))
        await session.execute(update(BotTarget).where(BotTarget.id.in_(owned)).values(enabled=False))
    await set_setting(session, "bot_clients", clients)
    return {"ok": True}


@router.post("/clients/{client_id}/rotate")
async def rotate_client(client_id: str, session: AsyncSession = Depends(get_session)):
    views = {row["id"]: row for row in await bot_clients.client_views(session)}
    if client_id not in views:
        raise HTTPException(404, "插件接入不存在")
    if await session.get(BotClient, client_id):
        raise HTTPException(409, "请由所属用户在账号中心重置 Token")
    _, key = await bot_clients.issue_key(session, views[client_id]["name"], client_id)
    return {"id": client_id, "key": key}


@router.put("/bridge")
async def save_bridge(body: BridgeBody, session: AsyncSession = Depends(get_session)):
    current = await get_setting(session, "bot_bridge") or {}
    current.update(endpoint=body.endpoint, api_key=body.api_key or current.get("api_key", ""))
    current.setdefault("tools_key", secrets.token_urlsafe(32))
    await set_setting(session, "bot_bridge", current)
    return {"ok": True}


@router.post("/targets")
async def add_target(body: TargetBody, session: AsyncSession = Depends(get_session)):
    await bot_preferences.validate_sources(session, bot_clients.LEGACY_CLIENT, [body])
    if await session.scalar(select(BotTarget.id).where(BotTarget.client_id == bot_clients.LEGACY_CLIENT, BotTarget.umo == body.umo)):
        raise HTTPException(409, "该会话已存在，请编辑已有目标")
    row = BotTarget(name=body.name, umo=body.umo, enabled=body.enabled,
                    config=body.model_dump(exclude={"name", "umo", "enabled"}))
    session.add(row)
    await session.commit()
    return target_view(row)


@router.put("/targets/{target_id}")
async def edit_target(target_id: int, body: TargetBody, session: AsyncSession = Depends(get_session)):
    row = await get_target(target_id, session)
    if row.managed_by_plugin:
        raise HTTPException(409, "此会话由 AstrBot 插件设置维护")
    if body.umo != row.umo:
        raise HTTPException(409, "变更收件人请新建会话，避免旧投递被重发给其他人")
    await apply_target(row, body, session)
    await session.commit()
    return target_view(row)


async def apply_target(row, body, session, *, check_quota=True):
    """配置同步与聊天修改共用，只有实际策略变化才推进扫描起点。"""
    await bot_preferences.validate_sources(session, row.client_id, [body])
    if check_quota and body.enabled and not row.enabled:
        from app.services.account_limits import check_bot_targets
        active = list(await session.scalars(select(BotTarget.umo).where(
            BotTarget.client_id == row.client_id, BotTarget.enabled.is_(True))))
        await check_bot_targets(session, row.client_id, [*active, body.umo])
    config = body.model_dump(exclude={"name", "umo", "enabled"})
    previous = TargetBody(name=row.name, umo=row.umo, enabled=row.enabled, **row.config).model_dump(exclude={"name", "umo", "enabled"})
    def comparable(value):
        selected = value.get("communities") or (list(COMMUNITIES) if value.get("community") == "all" else [value.get("community")])
        return {**value, "communities": sorted(selected), "channels": sorted(value.get("channels", []))}
    changed = comparable(config) != comparable(previous)
    # 重新启用或修改订阅范围时从现在开始，避免补发停用期间的旧消息。
    if body.enabled and (not row.enabled or changed):
        row.last_scan_at = utcnow()
    if changed or (row.enabled and not body.enabled):
        await session.execute(delete(SiteSetting).where(SiteSetting.key == f"bot_query_{row.id}"))
        await session.execute(delete(BotDelivery).where(BotDelivery.target_id == row.id, BotDelivery.status == "pending", BotDelivery.kind != "reminder"))
        # 已失败的旧范围消息保留审计，但不再自动重试。
        await session.execute(update(BotDelivery).where(BotDelivery.target_id == row.id, BotDelivery.status == "failed", BotDelivery.kind != "reminder").values(attempts=3))
    row.name, row.umo, row.enabled, row.config = body.name, body.umo, body.enabled, config


@router.delete("/targets/{target_id}")
async def remove_target(target_id: int, session: AsyncSession = Depends(get_session)):
    row = await get_target(target_id, session)
    if row.managed_by_plugin:
        raise HTTPException(409, "请在 AstrBot 插件设置中移除此会话，投递记录会保留")
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
    if not target.config.get("_plugin_configured", True):
        raise HTTPException(409, "此会话已从 AstrBot 插件设置中移除")
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
    client_id = await bot_clients.authenticate(session, x_bot_key)
    if client_id is None:
        raise HTTPException(401, "AniSignal Token 无效、已撤销或所属账号不可用，请在账号中心检查接入")
    return client_id


@tools_router.get("/identity")
async def tool_identity(session: AsyncSession = Depends(get_session), client_id: str = Depends(require_tools)):
    return await bot_clients.identity(session, client_id)


@tools_router.get("/catalog")
async def subscription_catalog(session: AsyncSession = Depends(get_session), client_id: str = Depends(require_tools)):
    return await bot_preferences.catalog(session, client_id)


@tools_router.get("/targets", dependencies=[Depends(require_tools)])
async def tool_targets(session: AsyncSession = Depends(get_session), client_id: str = Depends(require_tools)):
    # 返回全部会话（含已暂停）：插件在聊天里回显注册状态需要名称与启停标记，
    # 工具可用性由插件按 enabled 自行门禁。
    result = []
    for target in await session.scalars(select(BotTarget).where(BotTarget.client_id == client_id).order_by(BotTarget.id)):
        reason = await target_error(session, target)
        result.append({**target_view(target), "enabled": target.enabled and not reason, "authorization_error": reason})
    return result


@tools_router.get("/configuration")
async def export_configuration(session: AsyncSession = Depends(get_session), client_id: str = Depends(require_tools)):
    rows = await session.scalars(select(BotTarget).where(BotTarget.client_id == client_id,
        BotTarget.config["delivery_mode"].as_string() == "astrbot",
        # MySQL 的 JSON 布尔转换会把缺失字段变成 False，旧会话需单独包含。
        or_(BotTarget.config["_plugin_configured"].as_boolean().is_(True),
            BotTarget.config["_plugin_configured"].as_string().is_(None))).order_by(BotTarget.id))
    return {"targets": [TargetBody(name=t.name, umo=t.umo, enabled=t.enabled, **t.config).model_dump() for t in rows],
            "identity": await bot_clients.identity(session, client_id), "catalog": await bot_preferences.catalog(session, client_id)}


@tools_router.post("/configuration")
async def sync_configuration(body: ConfigurationBody, session: AsyncSession = Depends(get_session), client_id: str = Depends(require_tools)):
    from app.services.account_limits import check_bot_targets
    await check_bot_targets(session, client_id, [target.umo for target in body.targets if target.enabled])
    await bot_preferences.validate_sources(session, client_id, body.targets)
    rows = {row.umo: row for row in await session.scalars(select(BotTarget)
            .where(BotTarget.client_id == client_id).with_for_update())}
    for target in body.targets:
        existing = rows.get(target.umo)
        if existing and not existing.managed_by_plugin and existing.config.get("delivery_mode") != "astrbot":
            raise HTTPException(409, "该会话仍在旧直发通道中，请先停用旧通道")
    configured = []
    for target in body.targets:
        target = target.model_copy(update={"delivery_mode": "astrbot"})
        row = rows.get(target.umo)
        if row is None:
            row = BotTarget(client_id=client_id, name=target.name, umo=target.umo, enabled=target.enabled,
                            config=target.model_dump(exclude={"name", "umo", "enabled"}))
            session.add(row)
        else:
            await apply_target(row, target, session, check_quota=False)
        row.managed_by_plugin = True
        configured.append(row)
    selected = {target.umo for target in body.targets}
    for umo, row in rows.items():
        if umo not in selected and (row.managed_by_plugin or row.config.get("delivery_mode") == "astrbot"):
            await apply_target(row, TargetBody(name=row.name, umo=row.umo, enabled=False, **row.config), session)
            row.managed_by_plugin = True
            row.config = {**row.config, "_plugin_configured": False}
    await session.commit()
    return {"targets": [target_view(row) for row in configured], "identity": await bot_clients.identity(session, client_id),
            "catalog": await bot_preferences.catalog(session, client_id)}


@tools_router.post("/query", dependencies=[Depends(require_tools)])
async def tool_query(body: ToolQuery, session: AsyncSession = Depends(get_session), client_id: str = Depends(require_tools)):
    target = await session.scalar(select(BotTarget).where(BotTarget.client_id == client_id,
        BotTarget.umo == body.umo, BotTarget.enabled.is_(True)))
    if not target or body.tool not in target.config.get("tools", []):
        raise HTTPException(403, "此会话尚未启用该查询工具")
    if reason := await target_error(session, target):
        raise HTTPException(403, reason)
    # 旧插件查询未传 actor_id；既有可信接入兼容，新接入必须由真实消息携带。
    if not body.actor_id and (client_id != bot_clients.LEGACY_CLIENT or await session.get(BotClient, client_id)):
        raise HTTPException(403, "查询缺少真实操作者标识，请更新插件")
    if body.actor_id and (reason := actor_error(body.umo, body.actor_id)):
        raise HTTPException(403, reason)
    payload = await bot.query_target(session, target, body.tool, body.query)
    if payload.get("brief"):
        from app.services.custom_policy import insert_ignoring_duplicate

        query_id = secrets.token_hex(16)
        key = f"bot_query_{target.id}"
        value = {"id": query_id, "created_at": utcnow().isoformat(), "brief": jsonable_encoder(payload["brief"])}
        # 每个会话只保留最近一次清单，详情读取时再核对当前权限。
        await insert_ignoring_duplicate(session, SiteSetting, key=key, value={})
        await session.execute(update(SiteSetting).where(SiteSetting.key == key).values(value=value))
        await session.commit()
        payload["query_id"] = query_id
    return payload
