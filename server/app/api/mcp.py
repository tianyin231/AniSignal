"""AniSignal Streamable HTTP MCP（Model Context Protocol）服务端接口。

遵循 MCP 2025-06-18 / 2025-03-26 / 2024-11-05 无状态 Streamable HTTP 规范：
- POST /api/mcp（经 Web 代理对外同时暴露 /mcp）：处理 JSON-RPC 2.0 请求与批处理；
- GET  /api/mcp：若客户端请求 text/event-stream 返回 405（无状态服务端不维持 SSE 推送流），否则返回服务自描述元数据；
- DELETE /api/mcp：返回 204（无状态会话关闭）。
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import public_site_base
from app.db import get_session
from app.models import BotClient, User
from app.services import bot_clients
from app.services.mcp_tools import (
    McpContext,
    McpToolError,
    call_tool,
    get_prompt,
    list_prompts,
    list_resources,
    list_tools,
    read_resource,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_VERSION = "1.3.0"

_MAX_BODY_BYTES = 64 * 1024
_MAX_BATCH_SIZE = 16
_ANON_RATE_LIMIT = 60  # 每分钟每 IP
_USER_RATE_LIMIT = 120  # 每分钟每认证账号
_RATE_WINDOW_SECONDS = 60

# Redis 不可用（如 SQLite 本地测试）时的进程内滑窗兜底
_mem_rate_buckets: dict[str, deque[float]] = defaultdict(deque)

_SERVER_INSTRUCTIONS = (
    "AniSignal 是跨平台二游与 AI 资讯信号台，本 MCP 服务严格只读。"
    "提供活动日历、可用兑换码、官方公告实质修订（抓暗改）、聚合事件、周边商品履约证据、"
    "Steam 实时在线与口碑、AI 精选日报、热度飙升榜及全站跨语言检索。"
    "回答用户时请保留来源与北京时间（UTC+8）及链接；未收录的内容不可凭记忆编造，待核验线索必须标明尚未官方证实。"
)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


async def _check_rate_limit(request: Request, bucket_key: str, limit: int) -> bool:
    """检查每分钟滑动/固定窗口限流；返回 True 表示放行，False 表示超限。"""
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            window = int(time.time() // _RATE_WINDOW_SECONDS)
            rkey = f"mcp:rl:v1:{bucket_key}:{window}"
            count = await redis.incr(rkey)
            if count == 1:
                await redis.expire(rkey, _RATE_WINDOW_SECONDS + 5)
            return count <= limit
        except Exception:  # noqa: BLE001 - Redis 异常降级到进程内限流
            pass

    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SECONDS
    q = _mem_rate_buckets[bucket_key]
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    for header in ("x-api-key", "x-bot-key"):
        val = request.headers.get(header, "").strip()
        if val:
            return val
    return request.query_params.get("token", "").strip()


async def _resolve_user(session: AsyncSession, token: str) -> User | None:
    """支持使用个人 feed_token（账号中心 RSS/MCP 专属令牌）或用户 Bot Token 鉴权。"""
    if not token or len(token) > 256:
        return None

    # 1) 优先匹配 User.feed_token（常量时间比对防侧信道）
    user = await session.scalar(
        select(User).where(
            User.feed_token == token,
            User.status == "active",
            User.is_verified.is_(True),
        )
    )
    if user is not None and user.feed_token and secrets.compare_digest(user.feed_token, token):
        return user

    # 2) 兼容已签发的用户 BotClient Token（不接受旧版全局 legacy 管理员桥接密钥）
    client_id = await bot_clients.authenticate(session, token)
    if client_id and client_id != bot_clients.LEGACY_CLIENT:
        bot_client = await session.get(BotClient, client_id)
        if bot_client and bot_client.enabled:
            owner = await session.get(User, bot_client.user_id)
            if owner and owner.status == "active" and owner.is_verified:
                return owner
    return None


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _negotiate_protocol(requested: str | None) -> str:
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION


def _mcp_headers(protocol_version: str) -> dict[str, str]:
    return {
        "MCP-Protocol-Version": protocol_version,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


async def _dispatch_single(
    session: AsyncSession,
    ctx: McpContext,
    payload: Any,
    header_protocol: str,
) -> tuple[dict[str, Any] | None, str]:
    """处理单条 JSON-RPC 2.0 消息；通知返回 None。"""
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        req_id = payload.get("id") if isinstance(payload, dict) else None
        return _jsonrpc_error(req_id, -32600, "Invalid JSON-RPC 2.0 request"), header_protocol

    method = payload["method"]
    params = payload.get("params")
    is_notification = "id" not in payload
    req_id = payload.get("id")

    if method.startswith("notifications/"):
        return None, header_protocol

    if is_notification:
        return None, header_protocol

    if method == "initialize":
        req_ver = params.get("protocolVersion") if isinstance(params, dict) else None
        negotiated = _negotiate_protocol(req_ver)
        result = {
            "protocolVersion": negotiated,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {
                "name": "anisignal",
                "title": "AniSignal 信号台",
                "version": SERVER_VERSION,
            },
            "instructions": _SERVER_INSTRUCTIONS,
        }
        return _jsonrpc_result(req_id, result), negotiated

    if method == "ping":
        return _jsonrpc_result(req_id, {}), header_protocol

    if method == "tools/list":
        return _jsonrpc_result(req_id, {"tools": list_tools(ctx)}), header_protocol

    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _jsonrpc_error(req_id, -32602, "tools/call requires string 'name'"), header_protocol
        result = await call_tool(session, ctx, params["name"], params.get("arguments"))
        return _jsonrpc_result(req_id, result), header_protocol

    if method == "resources/list":
        return _jsonrpc_result(req_id, {"resources": list_resources(ctx)}), header_protocol

    if method == "resources/templates/list":
        return _jsonrpc_result(req_id, {"resourceTemplates": []}), header_protocol

    if method == "resources/read":
        if not isinstance(params, dict) or not isinstance(params.get("uri"), str):
            return _jsonrpc_error(req_id, -32602, "resources/read requires string 'uri'"), header_protocol
        try:
            result = await read_resource(session, ctx, params["uri"])
            return _jsonrpc_result(req_id, result), header_protocol
        except McpToolError as exc:
            return _jsonrpc_error(req_id, -32002, str(exc)), header_protocol

    if method == "prompts/list":
        return _jsonrpc_result(req_id, {"prompts": list_prompts()}), header_protocol

    if method == "prompts/get":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _jsonrpc_error(req_id, -32602, "prompts/get requires string 'name'"), header_protocol
        try:
            result = get_prompt(params["name"], params.get("arguments"))
            return _jsonrpc_result(req_id, result), header_protocol
        except McpToolError as exc:
            return _jsonrpc_error(req_id, -32602, str(exc)), header_protocol

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}"), header_protocol


@router.get("")
async def mcp_get(request: Request):
    """Streamable HTTP GET：SSE 请求按规范返回 405；浏览器或诊断请求返回自描述信息。"""
    accept = request.headers.get("accept", "").lower()
    if "text/event-stream" in accept and "application/json" not in accept and "text/html" not in accept:
        return Response(
            status_code=405,
            headers={"Allow": "POST, OPTIONS, DELETE", "Cache-Control": "no-store"},
        )
    base = public_site_base(request)
    anon_ctx = McpContext(user=None, base_url=base)
    return JSONResponse(
        content={
            "name": "anisignal",
            "title": "AniSignal 信号台 MCP Server",
            "version": SERVER_VERSION,
            "transport": "streamable-http",
            "stateless": True,
            "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "endpoint": f"{base}/mcp",
            "api_endpoint": f"{base}/api/mcp",
            "auth": {
                "anonymous": "支持免登录只读查询全部公开信号、日历、兑换码、官方修订、周边履约与 Steam 遥测",
                "authenticated": "通过 Authorization: Bearer <token>、X-API-Key 或 ?token=<token> 传入个人令牌，自动套用偏好并解锁个人关注流",
            },
            "tools": [t["name"] for t in list_tools(anon_ctx)],
            "resources": [r["uri"] for r in list_resources(anon_ctx)],
            "prompts": [p["name"] for p in list_prompts()],
        },
        headers=_mcp_headers(DEFAULT_PROTOCOL_VERSION),
    )


@router.delete("")
async def mcp_delete():
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.options("")
async def mcp_options():
    return Response(
        status_code=204,
        headers={
            "Allow": "POST, GET, DELETE, OPTIONS",
            "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization, X-API-Key, X-Bot-Key, MCP-Protocol-Version, Mcp-Session-Id",
            "Access-Control-Expose-Headers": "MCP-Protocol-Version",
        },
    )


@router.post("")
async def mcp_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content=_jsonrpc_error(None, -32600, "Request payload too large"),
            headers=_mcp_headers(DEFAULT_PROTOCOL_VERSION),
        )

    token = _extract_token(request)
    user: User | None = None
    if token:
        user = await _resolve_user(session, token)
        if user is None:
            return JSONResponse(
                status_code=401,
                content=_jsonrpc_error(None, -32001, "Token 无效、已重置或所属账号不可用，请在 AniSignal 账号中心检查。"),
                headers={**_mcp_headers(DEFAULT_PROTOCOL_VERSION), "WWW-Authenticate": "Bearer"},
            )

    bucket = f"u:{user.id}" if user is not None else f"ip:{_client_ip(request)}"
    limit = _USER_RATE_LIMIT if user is not None else _ANON_RATE_LIMIT
    if not await _check_rate_limit(request, bucket, limit):
        return JSONResponse(
            status_code=429,
            content=_jsonrpc_error(None, -32000, "请求过于频繁，请稍后重试。"),
            headers={**_mcp_headers(DEFAULT_PROTOCOL_VERSION), "Retry-After": "60"},
        )

    try:
        import json as _json

        body = _json.loads(raw_body.decode("utf-8")) if raw_body else None
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(None, -32700, "Parse error: invalid JSON"),
            headers=_mcp_headers(DEFAULT_PROTOCOL_VERSION),
        )

    req_proto = request.headers.get("mcp-protocol-version")
    proto = _negotiate_protocol(req_proto)
    ctx = McpContext(
        user=user,
        base_url=public_site_base(request),
        redis=getattr(request.app.state, "redis", None),
    )

    if isinstance(body, list):
        if not body or len(body) > _MAX_BATCH_SIZE:
            return JSONResponse(
                status_code=400,
                content=_jsonrpc_error(None, -32600, f"Invalid batch size (1..{_MAX_BATCH_SIZE})"),
                headers=_mcp_headers(proto),
            )
        responses = []
        for item in body:
            try:
                resp, proto = await _dispatch_single(session, ctx, item, proto)
            except Exception:  # noqa: BLE001 - 单条异常隔离，不泄露内部堆栈
                logger.exception("MCP batch item failed")
                item_id = item.get("id") if isinstance(item, dict) else None
                resp = _jsonrpc_error(item_id, -32603, "Internal server error")
            if resp is not None:
                responses.append(resp)
        if not responses:
            return Response(status_code=202, headers=_mcp_headers(proto))
        return JSONResponse(content=responses, headers=_mcp_headers(proto))

    try:
        resp, proto = await _dispatch_single(session, ctx, body, proto)
    except Exception:  # noqa: BLE001
        logger.exception("MCP request failed")
        req_id = body.get("id") if isinstance(body, dict) else None
        resp = _jsonrpc_error(req_id, -32603, "Internal server error")

    if resp is None:
        return Response(status_code=202, headers=_mcp_headers(proto))
    return JSONResponse(content=resp, headers=_mcp_headers(proto))
