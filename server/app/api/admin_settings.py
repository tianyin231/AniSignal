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
    from app.services.ai import _public_view, get_ai_config

    return {
        "smtp": smtp,
        "ai": _public_view(await get_ai_config(session)),
        "announcement": await settings_service.get_announcement(session),
        "texts": await settings_service.get_texts(session),
    }


@router.put("/ai")
async def put_ai(body: dict, session: AsyncSession = Depends(get_session)):
    """保存 LLM 配置（OpenAI 兼容）；api_key 留空保留旧值。"""
    stored = (await settings_service.get_setting(session, "ai")) or {}
    api_key = body.get("api_key")
    if api_key is None or api_key == "":
        api_key = stored.get("api_key") or ""
    value = {
        "base_url": str(body.get("base_url") or "").strip().rstrip("/"),
        "api_key": api_key,
        "model": str(body.get("model") or "").strip(),
    }
    await settings_service.set_setting(session, "ai", value)
    from app.services.ai import _public_view, get_ai_config

    return _public_view(await get_ai_config(session))


@router.post("/ai/test")
async def test_ai(session: AsyncSession = Depends(get_session)):
    """向已配置模型发一次极小补全，验证连通与鉴权。"""
    from app.services.ai import ai_available, chat, get_ai_config

    config = await get_ai_config(session)
    if not ai_available(config):
        return {"ok": False, "message": "未配置完整（需要 base_url / api_key / model）"}
    try:
        reply = await chat(config, "你是连通性测试。", "回复两个字：正常")
        return {"ok": True, "message": f"模型响应：{reply[:50]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"调用失败：{exc!r}"[:200]}


@router.post("/ai/summarize-all")
async def summarize_all_events(
    only_missing: bool = True, session: AsyncSession = Depends(get_session)
):
    """为事件补 AI 摘要（only_missing=True 只处理还没有 AI 摘要的事件）。"""
    from sqlalchemy import select as _select

    from app.models import Event
    from app.services.ai import summarize_event

    query = _select(Event.id).where(Event.is_active.is_(True))
    if only_missing:
        query = query.where(Event.summary.is_(None))
    ids = [row for row in await session.scalars(query)]
    done = skipped = failed = 0
    for event_id in ids[:200]:
        result = await summarize_event(session, event_id)
        status = result.get("status")
        if status == "ok":
            done += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
    return {"attempted": len(ids), "ok": done, "skipped": skipped, "failed": failed}


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
