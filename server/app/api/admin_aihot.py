"""复用平台管理员认证，访问原版引擎的信源、预算、模型和运行回执。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import require_admin
from app.services import aihot
from app.db import get_session
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/admin/aihot", dependencies=[Depends(require_admin)])


@router.get("/status")
async def status():
    if not aihot.bridge_enabled():
        return {"enabled": False, "read_enabled": False}
    return {"enabled": True, "read_enabled": aihot.enabled(),
        **await aihot.request("/internal/anisignal/overview")}


@router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT"])
async def admin_proxy(path: str, request: Request, session: AsyncSession = Depends(get_session)):
    allowed = {
        "GET": r"(?:sources(?:/[a-z0-9-]+)?|content(?:/[A-Za-z0-9_-]+)?)",
        "POST": r"(?:sources(?:/[a-z0-9-]+/fetch)?|content/[A-Za-z0-9_-]+/(?:visibility|override|rerun))",
        "PATCH": r"sources/[a-z0-9-]+",
        "PUT": r"(?:budgets|models)/[A-Za-z0-9_-]+",
    }
    if not re.fullmatch(allowed[request.method], path):
        raise HTTPException(404)
    body = await request.json() if request.method != "GET" else None
    result = await aihot.request("/internal/anisignal/" + path, method=request.method,
        params=dict(request.query_params), body=body)
    bridge = re.fullmatch(r"sources/anisignal-(x|bilibili)-(\d+)", path)
    if request.method == "PATCH" and bridge:
        from app.models import Source
        source = await session.get(Source, int(bridge[2]))
        patch = body.get("patch", {})
        if source and source.platform == bridge[1]:
            if "tier" in patch:
                source.editorial_tier = {"T1": "first_party", "T1_5": "near_source", "T2": "media"}.get(patch["tier"])
            if "owner_entity_id" in patch:
                source.owner_key = patch["owner_entity_id"]
            if "name" in patch:
                source.name = patch["name"]
            await session.commit()
    return result
