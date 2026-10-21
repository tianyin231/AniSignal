"""管理端站点设置：通知通道、全服公告、文案区域。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.api.admin import get_queue
from app.db import get_session
from app.services import settings as settings_service
from app.services.email_templates import notice_email
from app.services.mailer import send_email
from app.services.urls import safe_endpoint, safe_link

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/settings", dependencies=[Depends(require_admin)])


@router.get("")
async def get_all_settings(session: AsyncSession = Depends(get_session)):
    smtp = settings_service.smtp_public_view(await settings_service.get_effective_smtp(session))
    from app.services.ai import _public_view, get_ai_config

    return {
        "smtp": smtp,
        "onepush": settings_service.onepush_public_view(
            await settings_service.get_onepush(session)
        ),
        "ai": _public_view(await get_ai_config(session)),
        "announcement": await settings_service.get_announcement(session),
        "texts": await settings_service.get_texts(session),
        "media_visibility": await settings_service.get_media_visibility(session),
    }


@router.put("/ai")
async def put_ai(body: dict, session: AsyncSession = Depends(get_session)):
    """保存 LLM 配置（OpenAI 兼容）；api_key 与密钥池留空/缺省时保留旧值。"""
    stored = (await settings_service.get_setting(session, "ai")) or {}
    api_key = body.get("api_key")
    if api_key is None or api_key == "":
        api_key = stored.get("api_key") or ""
    raw_keys = body.get("api_keys")
    if raw_keys is None:
        api_keys = stored.get("api_keys") or []
    else:
        if isinstance(raw_keys, str):
            raw_keys = raw_keys.replace("，", ",").replace(",", "\n").split("\n")
        api_keys = [str(k).strip() for k in raw_keys if str(k).strip()]
    raw_base = str(body.get("base_url") or "").strip().rstrip("/")
    base_url = safe_endpoint(raw_base) or ""
    if raw_base and not base_url:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "base_url 必须是 http(s) 地址（自托管内网地址允许）",
        )
    value = {
        "base_url": base_url,
        "api_key": api_key,
        "api_keys": api_keys,
        "model": str(body.get("model") or "").strip(),
        "price_in": float(body.get("price_in") if body.get("price_in") is not None else stored.get("price_in") or 0),
        "price_out": float(body.get("price_out") if body.get("price_out") is not None else stored.get("price_out") or 0),
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
    only_missing: bool = True, session: AsyncSession = Depends(get_session), queue=Depends(get_queue)
):
    """为事件补 AI 摘要（only_missing=True 只处理还没有 AI 摘要的事件）。"""
    from app.api.admin_ai import summarize_all

    return await summarize_all(only_missing=only_missing, session=session, queue=queue)


@router.put("/smtp")
async def put_smtp(body: dict, session: AsyncSession = Depends(get_session)):
    """保存 SMTP；password 为空字符串或缺失时保留已存密码。"""
    stored = (await settings_service.get_setting(session, "smtp")) or {}
    password = body.get("password")
    if password is None or password == "":
        password = stored.get("password")  # 保留旧值（可能为空）
    port = int(body.get("port") or 587)
    tls_mode = str(body.get("tls_mode") or ("tls" if port == 465 else "starttls")).lower()
    if tls_mode not in {"starttls", "tls", "none"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "加密方式只支持 starttls / tls / none")
    value = {
        "host": str(body.get("host") or "").strip(),
        "port": port,
        "user": str(body.get("user") or "").strip(),
        "password": password or "",
        "from": str(body.get("from") or "").strip(),
        "tls_mode": tls_mode,
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
    text = "这是一封来自 AniSignal 管理端的 SMTP 配置测试邮件。\n收到本邮件说明当前 SMTP 配置可用。"
    sent = await send_email(
        to,
        "AniSignal SMTP 测试邮件",
        text,
        smtp=smtp,
        html_body=notice_email("SMTP 配置测试成功", text),
    )
    return {"sent": sent, "host": smtp["host"], "message": "测试邮件已发送，请查收" if sent else "发送失败：SMTP 未配置或不可用，查看服务端日志"}


@router.put("/onepush")
async def put_onepush(body: dict, session: AsyncSession = Depends(get_session)):
    """保存全站 OnePush；YAML 留空时保留已存参数。"""
    current = await settings_service.get_onepush(session)
    config = current["config"]
    config_yaml = str(body.get("config_yaml") or "").strip()
    if config_yaml:
        import yaml
        from onepush import get_notifier

        try:
            parsed = yaml.safe_load(config_yaml)
        except yaml.YAMLError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"YAML 格式错误：{exc!s}"[:200]) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "OnePush 配置必须是 YAML 对象")
        provider = str(parsed.get("provider") or "").strip()
        if not provider:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "OnePush 配置缺少 provider")
        if provider == "smtp":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮件请使用独立 SMTP 配置")
        try:
            get_notifier(provider)
        except Exception as exc:  # noqa: BLE001 - 转换为明确的配置错误
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不支持的 OnePush provider：{provider}") from exc
        config = parsed

    value = {
        "enabled": bool(body.get("enabled")) and bool(config),
        "config": config,
    }
    await settings_service.set_setting(session, "onepush", value)
    return settings_service.onepush_public_view(value)


@router.post("/onepush/test")
async def test_onepush(session: AsyncSession = Depends(get_session)):
    """用当前配置发送一条测试推送；即使通道未启用也可测试。"""
    from app.services.notifier import send_onepush

    return await send_onepush(
        "AniSignal OnePush 测试",
        "收到本消息说明当前 OnePush 配置可用。",
        await settings_service.get_onepush(session),
        force=True,
    )


@router.put("/announcement")
async def put_announcement(body: dict, session: AsyncSession = Depends(get_session)):
    text = str(body.get("text") or "").strip()
    level = str(body.get("level") or "info")
    if level not in ("info", "warning"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "level 只支持 info / warning")
    if len(text) > 500:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "公告内容不超过 500 字")
    raw_link = str(body.get("link") or "").strip()
    # 公告 link 会直接进前端 href 并无鉴权下发，非法形态必须显式拒绝而不是静默清空
    link = safe_link(raw_link) if raw_link else ""
    if raw_link and link is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "公告链接只接受站内路径（以 / 开头）或 http(s) 地址",
        )
    value = {
        "enabled": bool(body.get("enabled")) and bool(text),
        "level": level,
        "text": text,
        "link": link or "",
    }
    await settings_service.set_setting(session, "announcement", value)
    return value


@router.put("/media-visibility")
async def put_media_visibility(body: dict, session: AsyncSession = Depends(get_session)):
    """保存媒体可见性策略：default + platforms（按平台覆盖）。"""
    stored = await settings_service.get_media_visibility(session)
    default_in = body.get("default") or {}
    platforms = (body.get("platforms") or {}) if "platforms" in body else stored.get("platforms", {})
    value = {
        "default": {
            "image": str(default_in.get("image") or stored.get("default", {}).get("image") or "show"),
            "video": str(default_in.get("video") or stored.get("default", {}).get("video") or "show"),
        },
        "platforms": platforms,
    }
    normalized = settings_service._normalize_media_visibility(value)
    await settings_service.set_setting(session, "media_visibility", normalized)
    return normalized


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
