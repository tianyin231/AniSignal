"""统一通知传输：全站 OnePush + 可选用户邮件。"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import settings as settings_service
from app.services.mailer import send_email

logger = logging.getLogger(__name__)


async def send_onepush(
    title: str,
    content: str,
    onepush: dict,
    *,
    force: bool = False,
) -> dict:
    """发送一次 OnePush；配置关闭时跳过，管理端测试可用 force 绕过开关。"""
    config = onepush.get("config") if isinstance(onepush.get("config"), dict) else {}
    provider = str(config.get("provider") or "").strip()
    if not provider:
        return {"ok": False, "status": "not_configured", "message": "OnePush 尚未配置"}
    if not onepush.get("enabled") and not force:
        return {"ok": False, "status": "disabled", "message": "OnePush 当前未启用"}

    params = {key: value for key, value in config.items() if key != "provider"}
    params.update(title=title, content=content)
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_send_onepush_sync, provider, params),
            timeout=30,
        )
    except TimeoutError:
        logger.warning("OnePush provider %s timed out", provider)
        return {"ok": False, "status": "timeout", "message": "OnePush 请求超时"}
    except Exception as exc:  # noqa: BLE001 - 单个推送失败不能影响主流程
        logger.warning("OnePush provider %s failed: %r", provider, exc)
        return {"ok": False, "status": "failed", "message": f"OnePush 发送失败：{exc!s}"[:200]}

    if response is None:
        return {"ok": False, "status": "failed", "message": "OnePush 未收到服务端响应"}
    status_code = int(getattr(response, "status_code", 0) or 0)
    ok = 200 <= status_code < 300
    return {
        "ok": ok,
        "status": "sent" if ok else "failed",
        "message": f"OnePush 已发送（HTTP {status_code}）" if ok else f"OnePush 返回 HTTP {status_code}",
    }


def _send_onepush_sync(provider: str, params: dict):
    from onepush import notify

    return notify(provider, **params)


async def send_information(
    session: AsyncSession,
    title: str,
    content: str,
    *,
    email_to: str | None = None,
) -> dict:
    """统一入口；OnePush 发全站管理员，邮件按明确收件人发送。"""
    onepush_result = await send_onepush(
        title,
        content,
        await settings_service.get_onepush(session),
    )
    email_result = None
    if email_to:
        email_result = await send_email(
            email_to,
            title,
            content,
            smtp=await settings_service.get_effective_smtp(session),
        )
    return {"onepush": onepush_result, "email": email_result}
