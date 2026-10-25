"""插件接入密钥只授权本实例的会话，不授予站点管理员权限。"""

import hashlib
import secrets
from datetime import datetime, timedelta

from app.clock import utcnow
from app.services.settings import get_setting, set_setting

LEGACY_CLIENT = "legacy"


def setting_key(name: str, client_id: str) -> str:
    return name if client_id == LEGACY_CLIENT else f"{name}_{client_id}"


async def authenticate(session, token: str) -> str | None:
    clients = await get_setting(session, "bot_clients") or {}
    legacy = clients.get(LEGACY_CLIENT, {})
    expected = (await get_setting(session, "bot_bridge") or {}).get("tools_key", "")
    if expected and legacy.get("enabled", True) and secrets.compare_digest(token, expected):
        return LEGACY_CLIENT
    if not token.startswith("asb_"):
        return None
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None
    client = clients.get(parts[1], {})
    digest = hashlib.sha256(token.encode()).hexdigest()
    if client.get("enabled") and secrets.compare_digest(digest, client.get("key_hash", "")):
        return parts[1]
    return None


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
        runtime = await get_setting(session, setting_key("bot_runtime", client_id)) or {}
        last_seen = runtime.get("last_seen")
        seen_at = datetime.fromisoformat(last_seen) if last_seen else None
        rows.append({"id": client_id, "name": config["name"], "enabled": config.get("enabled", True),
                     "version": runtime.get("version", ""), "last_seen": last_seen,
                     "online": bool(config.get("enabled", True) and seen_at and seen_at > utcnow() - timedelta(minutes=2))})
    return rows
