"""Admin 鉴权：兼容管理员 Token（X-Admin-Token）与已绑定的站点管理员会话（anisignal_session）。"""

import secrets
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.services.auth import SESSION_COOKIE, get_user_by_token
from app.services.queue import ArqQueue, BaseQueue, LocalQueue
from app.services.settings import get_admin_user_ids


async def get_queue(request: Request) -> BaseQueue:
    """任务队列：优先用 app.state 上已初始化的（本地模式由 lifespan 创建，
    测试由 fixture 注入）；否则按 local_mode 懒创建 arq 或本地实现。"""
    queue = getattr(request.app.state, "queue", None)
    if queue is None:
        if get_settings().local_mode:
            queue = LocalQueue()
        else:
            from app.services.collect import get_redis_pool

            queue = ArqQueue(await get_redis_pool())
        request.app.state.queue = queue
    return queue

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _check_admin_cookie_csrf(request: Request) -> None:
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="cross-origin admin request blocked")
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return
    origin_host = urlsplit(origin).netloc.lower()
    allowed = {
        h
        for h in (
            (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower(),
            (request.headers.get("host") or "").split(",")[0].strip().lower(),
            urlsplit(get_settings().public_base_url).netloc.lower(),
        )
        if h
    }
    if not origin_host or origin_host not in allowed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="cross-origin admin request blocked")


async def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None, description="管理员 Token"),
    session: AsyncSession = Depends(get_session),
) -> None:
    if x_admin_token is not None:
        expected = get_settings().admin_token
        if expected and expected != "change-me" and secrets.compare_digest(x_admin_token, expected):
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _check_admin_cookie_csrf(request)
        user = await get_user_by_token(session, token)
        if (
            user is not None
            and user.status == "active"
            and user.is_verified
            and user.id in await get_admin_user_ids(session)
        ):
            return

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
