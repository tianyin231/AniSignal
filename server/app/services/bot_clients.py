"""插件接入密钥只授权本实例的会话，不授予站点管理员权限。"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from app.clock import utcnow
from app.models import BotClient, BotDelivery, BotTarget, User
from app.services.settings import get_setting, set_setting

LEGACY_CLIENT = "legacy"


def setting_key(name: str, client_id: str) -> str:
    return name if client_id == LEGACY_CLIENT else f"{name}_{client_id}"


async def authenticate(session, token: str) -> str | None:
    if token.startswith("asu_"):
        parts = token.split("_", 2)
        if len(parts) != 3:
            return None
        row = await session.scalar(select(BotClient).join(User, User.id == BotClient.user_id).where(
            BotClient.id == parts[1], BotClient.enabled.is_(True), User.status == "active", User.is_verified.is_(True)))
        if row and secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row.token_hash):
            return row.id
        return None
    clients = await get_setting(session, "bot_clients") or {}
    legacy = clients.get(LEGACY_CLIENT, {})
    expected = (await get_setting(session, "bot_bridge") or {}).get("tools_key", "")
    # 已绑定用户的接入不再接受旧插件密钥，轮换/撤销不会被旧密钥绕过。
    legacy_bound = await session.get(BotClient, LEGACY_CLIENT)
    if expected and not legacy_bound and legacy.get("enabled", True) and secrets.compare_digest(token, expected):
        return LEGACY_CLIENT
    if not token.startswith("asb_"):
        return None
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None
    client = clients.get(parts[1], {})
    if await session.get(BotClient, parts[1]):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    if client.get("enabled") and secrets.compare_digest(digest, client.get("key_hash", "")):
        return parts[1]
    return None


def user_token(row: BotClient) -> str:
    token = f"asu_{row.id}_{secrets.token_urlsafe(32)}"
    row.token_hash = hashlib.sha256(token.encode()).hexdigest()
    row.token_tail = token[-4:]
    row.rotated_at = utcnow()
    return token


async def create_user_client(session, user: User, name: str, *, client_id: str | None = None):
    row = BotClient(id=client_id or secrets.token_hex(12), user_id=user.id, name=name, enabled=True)
    token = user_token(row)
    session.add(row)
    await session.commit()
    return row, token


async def revoke_user_client(session, row: BotClient):
    row.enabled = False
    owned = select(BotTarget.id).where(BotTarget.client_id == row.id)
    await session.execute(update(BotDelivery).where(BotDelivery.target_id.in_(owned),
        BotDelivery.status.in_(["pending", "failed"])).values(status="cancelled", receipt=None, error="用户已撤销接入 Token", updated_at=utcnow()))
    await session.execute(update(BotTarget).where(BotTarget.client_id == row.id).values(enabled=False))
    await session.commit()


async def identity(session, client_id: str) -> dict:
    row = await session.get(BotClient, client_id)
    if row:
        from app.services.account_limits import quota_view
        user = await session.get(User, row.user_id)
        return {"client_id": row.id, "name": row.name, "user": {"id": user.id,
            "display_name": user.display_name or user.email.split("@", 1)[0]}, "legacy": False,
            "quota": await quota_view(session, row.user_id)}
    config = (await get_setting(session, "bot_clients") or {}).get(client_id, {})
    return {"client_id": client_id, "name": config.get("name", "现有 AstrBot"), "user": None, "legacy": True}


async def user_client_views(session, user_id: int) -> list[dict]:
    owned = select(BotClient.id).where(BotClient.user_id == user_id)
    counts = dict((await session.execute(select(BotTarget.client_id, func.count()).where(
        BotTarget.client_id.in_(owned)).group_by(BotTarget.client_id))).all())
    result = []
    for row in await session.scalars(select(BotClient).where(BotClient.user_id == user_id).order_by(BotClient.created_at.desc())):
        runtime = await get_setting(session, setting_key("bot_runtime", row.id)) or {}
        last_seen = runtime.get("last_seen")
        result.append({"id": row.id, "name": row.name, "enabled": row.enabled, "token_tail": row.token_tail,
            "created_at": row.created_at, "rotated_at": row.rotated_at, "last_seen": last_seen,
            "online": bool(row.enabled and last_seen and datetime.fromisoformat(last_seen) > utcnow() - timedelta(minutes=2)),
            "version": runtime.get("version", ""), "target_count": counts.get(row.id, 0)})
    return result


async def issue_key(session, name: str, client_id: str | None = None) -> tuple[str, str]:
    clients = dict(await get_setting(session, "bot_clients") or {})
    client_id = client_id or secrets.token_hex(12)
    token = f"asb_{client_id}_{secrets.token_urlsafe(32)}"
    previous = clients.get(client_id, {})
    clients[client_id] = {**previous, "name": name, "enabled": previous.get("enabled", True),
                          "created_at": previous.get("created_at", utcnow().isoformat()),
                          "key_hash": hashlib.sha256(token.encode()).hexdigest()}
    if client_id == LEGACY_CLIENT:
        bridge = dict(await get_setting(session, "bot_bridge") or {})
        bridge["tools_key"] = token
        await set_setting(session, "bot_bridge", bridge)
    await set_setting(session, "bot_clients", clients)
    return client_id, token


async def client_views(session) -> list[dict]:
    clients = dict(await get_setting(session, "bot_clients") or {})
    if (await get_setting(session, "bot_bridge") or {}).get("tools_key"):
        clients.setdefault(LEGACY_CLIENT, {"name": "现有 AstrBot", "enabled": True})
    rows = []
    for client_id, config in clients.items():
        if await session.get(BotClient, client_id):
            continue
        runtime = await get_setting(session, setting_key("bot_runtime", client_id)) or {}
        last_seen = runtime.get("last_seen")
        seen_at = datetime.fromisoformat(last_seen) if last_seen else None
        rows.append({"id": client_id, "name": config["name"], "enabled": config.get("enabled", True),
                     "version": runtime.get("version", ""), "last_seen": last_seen,
                     "online": bool(config.get("enabled", True) and seen_at and seen_at > utcnow() - timedelta(minutes=2))})
    owners = set(await session.scalars(select(BotClient.user_id).distinct()))
    for user_id in owners:
        rows.extend({**row, "owner_id": user_id} for row in await user_client_views(session, user_id))
    return rows
