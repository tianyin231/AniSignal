"""复用平台管理员认证，管理原生 AI 资讯信源、模型、额度与内容状态。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue, require_admin
from app.db import get_session
from app.services import news_admin

router = APIRouter(prefix="/api/admin/aihot", dependencies=[Depends(require_admin)])


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)):
    return await news_admin.status(session)


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
    queue = await get_queue(request) if request.method == "POST" and path.endswith(("/fetch", "/rerun")) else None
    return await news_admin.request(
        session, path, method=request.method, params=dict(request.query_params), body=body or {}, queue=queue
    )
