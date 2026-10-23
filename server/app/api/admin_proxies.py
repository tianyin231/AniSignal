"""管理员维护代理；内部租用接口仅供 AIHOT 使用，不向管理界面返回密码。"""

import ipaddress
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.clock import utcnow
from app.config import get_settings
from app.db import get_session
from app.services import collector_proxy as pool
from app.services.settings import set_setting

router = APIRouter(prefix="/api/admin/proxies", dependencies=[Depends(require_admin)])
internal_router = APIRouter(prefix="/api/internal/collector-proxy")


class PoolOptions(BaseModel):
    enabled: bool
    platforms: dict[str, bool] = Field(default_factory=dict)
    order: list[str] | None = None


class ProxyImport(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    scheme: str = "http"
    label: str = Field(default="", max_length=120)


class ProxyEdit(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    scheme: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None  # 缺省保留；显式空字符串可清除。
    enabled: bool | None = None


def find_entry(config, entry_id):
    entry = next((entry for entry in config["entries"] if entry["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(404, "代理不存在")
    return entry


@router.get("")
async def get_pool(response: Response, session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return pool.public_config(await pool.get_config(session))


@router.put("")
async def save_options(body: PoolOptions, session: AsyncSession = Depends(get_session)):
    config = await pool.get_config(session)
    if any(p not in pool.PLATFORMS for p in body.platforms):
        raise HTTPException(422, "未知的采集平台")
    if body.enabled and not any(entry.get("enabled") for entry in config["entries"]):
        raise HTTPException(400, "请先添加并启用至少一个代理")
    if body.order is not None:
        expected = {"direct", *(entry["id"] for entry in config["entries"])}
        if len(body.order) != len(expected) or set(body.order) != expected:
            raise HTTPException(422, "优先级须包含直连和每个代理各一次，请刷新后重试")
        config["order"] = body.order
    config.update(enabled=body.enabled, platforms={**config["platforms"], **body.platforms})
    await set_setting(session, pool.SETTING_KEY, config)
    return pool.public_config(config)


@router.post("")
async def import_proxies(body: ProxyImport, session: AsyncSession = Depends(get_session)):
    config = await pool.get_config(session)
    known = {pool.proxy_url(entry) for entry in config["entries"]}
    added, duplicates, errors = 0, 0, []
    for line_number, line in enumerate(body.text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            entry = pool.parse_proxy(line, body.scheme)
            entry["label"] = body.label.strip()
        except ValueError as exc:
            errors.append({"line": line_number, "message": str(exc)})
            continue
        url = pool.proxy_url(entry)
        if url in known:
            duplicates += 1
            continue
        config["entries"].append(entry)
        config["order"].append(entry["id"])
        known.add(url)
        added += 1
    await set_setting(session, pool.SETTING_KEY, config)
    return {"added": added, "duplicates": duplicates, "errors": errors, "pool": pool.public_config(config)}


@router.patch("/{entry_id}")
async def edit_proxy(entry_id: str, body: ProxyEdit, session: AsyncSession = Depends(get_session)):
    config = await pool.get_config(session)
    entry = find_entry(config, entry_id)
    patch = body.model_dump(exclude_none=True)
    updated = {**entry, **patch}
    try:
        normalized = pool.parse_proxy(pool.proxy_url(updated))
    except (ValueError, KeyError):
        raise HTTPException(422, "代理地址或认证信息格式错误") from None
    entry.update({**normalized, "id": entry_id, "label": updated.get("label", ""), "enabled": updated["enabled"]})
    entry.pop("last_test", None)
    if not any(item.get("enabled") for item in config["entries"]):
        config["enabled"] = False
    await set_setting(session, pool.SETTING_KEY, config)
    return pool.public_config(config)


@router.delete("/{entry_id}")
async def delete_proxy(entry_id: str, session: AsyncSession = Depends(get_session)):
    config = await pool.get_config(session)
    find_entry(config, entry_id)
    config["entries"] = [entry for entry in config["entries"] if entry["id"] != entry_id]
    config["order"] = [key for key in config["order"] if key != entry_id]
    if not any(entry.get("enabled") for entry in config["entries"]):
        config["enabled"] = False
    await set_setting(session, pool.SETTING_KEY, config)
    return pool.public_config(config)


@router.post("/{entry_id}/test")
async def test_proxy(entry_id: str, session: AsyncSession = Depends(get_session)):
    config = await pool.get_config(session)
    entry = find_entry(config, entry_id)
    started = time.monotonic()
    result = {"ok": False, "checked_at": utcnow().isoformat(), "message": "代理连接失败"}
    try:
        async with httpx.AsyncClient(proxy=pool.proxy_url(entry), timeout=15, trust_env=False) as client:
            response = await client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            result.update(ok=True, exit_ip=str(ipaddress.ip_address(response.json()["ip"])), message="代理连接正常")
    except Exception as exc:
        result["message"] = "代理认证失败（407）" if "407" in str(exc) else f"代理连接失败（{type(exc).__name__}）"
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    entry["last_test"] = result
    await set_setting(session, pool.SETTING_KEY, config)
    return result


async def require_bridge(x_anisignal_key: str = Header(default="")):
    token = get_settings().aihot_token
    if len(token) < 32 or not secrets.compare_digest(x_anisignal_key, token):
        raise HTTPException(401, "invalid bridge token")


@internal_router.post("/lease", dependencies=[Depends(require_bridge)])
async def lease_proxy(response: Response, session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return {"routes": await pool.collection_routes(session, "aihot")}
