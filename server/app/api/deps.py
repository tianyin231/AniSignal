"""Admin 鉴权：单管理员 Token（env ANISIGNAL_ADMIN_TOKEN），常量时间比较。"""

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_admin(x_admin_token: str = Header(..., description="管理员 Token")) -> None:
    expected = get_settings().admin_token
    if not expected or expected == "change-me" or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
