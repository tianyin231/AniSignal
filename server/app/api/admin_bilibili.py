"""管理端B站网页登录；密码与短信验证码不落库。"""

from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
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
    cid: int = Field(default=1, ge=1, le=1000)
    phone: str = Field(pattern=r"^[0-9]{5,20}$")


class SmsLogin(BaseModel):
    cid: int = Field(default=1, ge=1, le=1000)
    phone: str = Field(pattern=r"^[0-9]{5,20}$")
    code: SecretStr = Field(min_length=4, max_length=8)
    captcha_key: SecretStr = Field(min_length=1, max_length=256)


async def _result(operation: Awaitable[Any]):
    try:
        return await operation
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except httpx.HTTPError:
        raise HTTPException(502, "B站登录接口暂时不可用，请稍后重试或使用扫码登录") from None


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
