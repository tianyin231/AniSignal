"""管理端站点设置：SMTP 热配置（含测试发信）、全服公告、文案区域。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db import get_session
from app.services import settings as settings_service
from app.services.mailer import send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/settings", dependencies=[Depends(require_admin)])


@router.get("")
async def get_all_settings(session: AsyncSession = Depends(get_session)):
    smtp = settings_service.smtp_public_view(await settings_service.get_effective_smtp(session))
    return {
        "smtp": smtp,
        "announcement": await settings_service.get_announcement(session),
        "texts": await settings_service.get_texts(session),
    }


@router.put("/smtp")
async def put_smtp(body: dict, session: AsyncSession = Depends(get_session)):
    """保存 SMTP；password 为空字符串或缺失时保留已存密码。"""
    stored = (await settings_service.get_setting(session, "smtp")) or {}
    password = body.get("password")
    if password is None or password == "":
        password = stored.get("password")  # 保留旧值（可能为空）
    value = {
        "host": str(body.get("host") or "").strip(),
        "port": int(body.get("port") or 587),
        "user": str(body.get("user") or "").strip(),
        "password": password or "",
        "from": str(body.get("from") or "").strip(),
        "starttls": bool(body.get("starttls", True)),
    }
    await settings_service.set_setting(session, "smtp", value)
    return settings_service.smtp_public_view(await settings_service.get_effective_smtp(session))


@router.post("/smtp/test")
async def test_smtp(body: dict, session: AsyncSession = Depends(get_session)):
    """用当前生效配置给指定邮箱发测试邮件。"""
    to = str(body.get("to") or "").strip()
    if not to or "@" not in to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写有效的收件邮箱")
    smtp = await settings_service.get_effective_smtp(session)
    sent = await send_email(
        to,
        "AniSignal SMTP 测试邮件",
        "这是一封来自 AniSignal 管理端的 SMTP 配置测试邮件。\n收到本邮件说明当前 SMTP 配置可用。",
        smtp=smtp,
    )
    return {"sent": sent, "host": smtp["host"], "message": "测试邮件已发送，请查收" if sent else "发送失败：SMTP 未配置或不可用，查看服务端日志"}


@router.put("/announcement")
async def put_announcement(body: dict, session: AsyncSession = Depends(get_session)):
    text = str(body.get("text") or "").strip()
    level = str(body.get("level") or "info")
    if level not in ("info", "warning"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "level 只支持 info / warning")
    if len(text) > 500:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "公告内容不超过 500 字")
    value = {
        "enabled": bool(body.get("enabled")) and bool(text),
        "level": level,
        "text": text,
        "link": str(body.get("link") or "").strip(),
    }
    await settings_service.set_setting(session, "announcement", value)
    return value


@router.put("/texts")
async def put_texts(body: dict, session: AsyncSession = Depends(get_session)):
    """更新文案区域；只接受白名单 key。"""
    allowed = settings_service.DEFAULT_TEXTS
    unknown = [k for k in body if k not in allowed]
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"未知的文案区域：{', '.join(unknown[:5])}"
        )
    stored = (await settings_service.get_setting(session, "texts")) or {}
    for key, value in body.items():
        if value is not None:
            stored[key] = str(value)[:1000]
    await settings_service.set_setting(session, "texts", stored)
    return await settings_service.get_texts(session)
