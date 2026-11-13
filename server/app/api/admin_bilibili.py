"""管理端 B 站授权会话：状态检测、扫码登录、网页账密/短信登录、手动粘贴与清除凭据（密码与验证码不落库）。"""

from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
import httpx
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
from app.services import bilibili_session as session_service
from app.services import bilibili_web_login as login

router = APIRouter(prefix="/api/admin/bilibili-session", dependencies=[Depends(require_admin)])


class CaptchaVerification(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    challenge: str = Field(min_length=1, max_length=256)
    validation: str = Field(alias="validate", min_length=1, max_length=256)
    seccode: str = Field(min_length=1, max_length=256)


class PasswordLogin(CaptchaVerification):
    username: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)


class SmsSend(CaptchaVerification):
    cid: int = Field(default=86, ge=1, le=9999)
    phone: str = Field(pattern=r"^[0-9]{5,20}$")


class SmsLogin(BaseModel):
    cid: int = Field(default=86, ge=1, le=9999)
    phone: str = Field(pattern=r"^[0-9]{5,20}$")
    code: SecretStr = Field(min_length=4, max_length=8)
    captcha_key: SecretStr = Field(min_length=1, max_length=256)


async def _result(operation: Awaitable[Any]):
    try:
        return await operation
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except httpx.HTTPError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "B站登录接口暂时不可用，请稍后重试或使用扫码登录"
        ) from None


@router.get("")
async def bilibili_session_status(session: AsyncSession = Depends(get_session)):
    return await session_service.get_status(session)


@router.post("/check")
async def check_bilibili_session(session: AsyncSession = Depends(get_session)):
    return await session_service.check_status(session)


@router.post("/qrcode")
async def create_bilibili_qrcode():
    try:
        return await session_service.generate_qr_login()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc) or "生成扫码会话失败") from exc


@router.get("/qrcode/{qrcode_key}")
async def poll_bilibili_qrcode(qrcode_key: str, session: AsyncSession = Depends(get_session)):
    try:
        return await session_service.poll_qr_login(session, qrcode_key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "查询扫码状态失败，请重试") from exc


@router.put("/cookie")
async def set_bilibili_cookie(body: dict, session: AsyncSession = Depends(get_session)):
    raw_cookie = str(body.get("cookie") or "").strip()
    if not raw_cookie:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请粘贴包含 SESSDATA 的 Cookie 内容")
    try:
        return await session_service.save_manual_cookie(session, raw_cookie)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.delete("")
async def delete_bilibili_session(session: AsyncSession = Depends(get_session)):
    return await session_service.clear_credentials(session)


@router.post("/captcha")
async def create_captcha():
    return await _result(login.create_captcha())


@router.get("/countries")
async def countries():
    return await _result(login.country_list())


@router.post("/login/password")
async def password_login(body: PasswordLogin, session: AsyncSession = Depends(get_session)):
    captcha = body.model_dump(include=set(CaptchaVerification.model_fields), by_alias=True)
    return await _result(login.login_password(session, body.username.strip(), body.password.get_secret_value(), captcha))


@router.post("/sms/send")
async def send_sms(body: SmsSend):
    captcha = body.model_dump(include=set(CaptchaVerification.model_fields), by_alias=True)
    return await _result(login.send_sms(body.cid, body.phone, captcha))


@router.post("/login/sms")
async def sms_login(body: SmsLogin, session: AsyncSession = Depends(get_session)):
    return await _result(login.login_sms(
        session, body.cid, body.phone, body.code.get_secret_value(), body.captcha_key.get_secret_value(),
    ))
