"""账号 API：注册/验证/登录/登出/改密/找回。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.db import get_session
from app.models import EmailCode, User
from app.services.auth import (
    AuthError,
    SESSION_COOKIE,
    clear_login_failures,
    create_session,
    hash_password,
    issue_code,
    record_login_failure,
    revoke_all_sessions,
    revoke_current_session,
    validate_credentials,
    verify_code,
    verify_password,
    _check_login_lock,
)
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")

# 防枚举：注册/找回的响应不区分"邮箱已存在/不存在"
_REGISTER_OK = {"message": "如果邮箱可用，验证码已发送，请查收（含垃圾邮件箱）"}


def _auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/register")
async def register(body: dict, session: AsyncSession = Depends(get_session)):
    try:
        email, password = validate_credentials(str(body.get("email", "")), str(body.get("password", "")))
    except AuthError as exc:
        raise _auth_error(exc)

    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        if existing.is_verified:
            # 已激活账号：不暴露存在性，也不发码
            return _REGISTER_OK
        # 未验证的重复注册：更新密码并重发码
        existing.password_hash = hash_password(password)
        user = existing
    else:
        user = User(email=email, password_hash=hash_password(password))
        session.add(user)
        await session.flush()
    try:
        await issue_code(session, user, "register")
    except AuthError as exc:
        await session.rollback()
        raise _auth_error(exc)
    await session.commit()
    return _REGISTER_OK


@router.post("/verify")
async def verify(body: dict, response: Response, session: AsyncSession = Depends(get_session)):
    email = str(body.get("email", "")).strip().lower()
    code = str(body.get("code", ""))
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status_code=400, detail="验证码不正确或已过期")
    try:
        await verify_code(session, user, "register", code)
    except AuthError as exc:
        raise _auth_error(exc)
    user.is_verified = True
    user.last_login_at = utcnow()
    token = await create_session(session, user)
    _set_session_cookie(response, token)
    return {"message": "验证成功，已登录", "email": user.email}


@router.post("/resend")
async def resend(body: dict, session: AsyncSession = Depends(get_session)):
    email = str(body.get("email", "")).strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or user.is_verified:
        return _REGISTER_OK
    try:
        await issue_code(session, user, "register")
    except AuthError as exc:
        await session.rollback()
        raise _auth_error(exc)
    await session.commit()
    return _REGISTER_OK


@router.post("/login")
async def login(body: dict, response: Response, session: AsyncSession = Depends(get_session)):
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    try:
        _check_login_lock(email)
    except AuthError as exc:
        raise _auth_error(exc)
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        try:
            record_login_failure(email)
        except AuthError as exc:
            raise _auth_error(exc)
        # 统一文案：不区分"账号不存在"与"密码错误"
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号不可用")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="邮箱未验证，请先完成验证")
    clear_login_failures(email)
    user.last_login_at = utcnow()
    token = await create_session(session, user)
    _set_session_cookie(response, token)
    return {"message": "登录成功", "email": user.email}


@router.post("/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await revoke_current_session(session, token)
    response.delete_cookie(SESSION_COOKIE)
    return {"message": "已退出"}


@router.get("/me")
async def me(request: Request, session: AsyncSession = Depends(get_session)):
    user = await _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar": user.avatar,
        "is_verified": user.is_verified,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


@router.put("/profile")
async def update_profile(
    body: dict,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    """修改昵称与头像（头像为预设样式库 key，白名单校验）。"""
    display_name = body.get("display_name")
    avatar = body.get("avatar")
    if display_name is not None:
        display_name = str(display_name).strip()
        if len(display_name) > 24:
            raise HTTPException(status_code=400, detail="昵称不超过 24 个字符")
        user.display_name = display_name or None
    if avatar is not None:
        from app.services.avatars import AVATAR_IDS

        avatar = str(avatar)
        if avatar not in AVATAR_IDS:
            raise HTTPException(status_code=400, detail="不支持的头像样式")
        user.avatar = avatar
    await session.commit()
    return {"display_name": user.display_name, "avatar": user.avatar}


@router.post("/password/change")
async def change_password(
    body: dict,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    user = await _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    old = str(body.get("old_password", ""))
    new = str(body.get("new_password", ""))
    if not verify_password(old, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    try:
        _, new = validate_credentials(user.email, new)
    except AuthError as exc:
        raise _auth_error(exc)
    user.password_hash = hash_password(new)
    await revoke_all_sessions(session, user.id)  # 改密后全部会话失效
    token = await create_session(session, user)
    _set_session_cookie(response, token)  # 当前设备免重登
    return {"message": "密码已修改"}


@router.post("/password/forgot")
async def forgot(body: dict, session: AsyncSession = Depends(get_session)):
    email = str(body.get("email", "")).strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_verified:
        return _REGISTER_OK  # 防枚举
    try:
        await issue_code(session, user, "reset")
    except AuthError as exc:
        await session.rollback()
        raise _auth_error(exc)
    await session.commit()
    return _REGISTER_OK


@router.post("/password/reset")
async def reset(body: dict, session: AsyncSession = Depends(get_session)):
    email = str(body.get("email", "")).strip().lower()
    code = str(body.get("code", ""))
    new = str(body.get("new_password", ""))
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status_code=400, detail="验证码不正确或已过期")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号不可用")
    try:
        await verify_code(session, user, "reset", code)
        _, new = validate_credentials(email, new)
    except AuthError as exc:
        raise _auth_error(exc)
    user.password_hash = hash_password(new)
    await revoke_all_sessions(session, user.id)
    return {"message": "密码已重置，请用新密码登录"}


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        # 上 HTTPS 后建议开启 secure
    )


async def _current_user(request: Request, session: AsyncSession) -> User | None:
    from app.services.auth import get_user_by_token

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await get_user_by_token(session, token)


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    """FastAPI 依赖：需要登录的端点使用。"""
    user = await _current_user(request, session)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


