"""邮件发送：通用 SMTP（aiosmtplib），支持运行时（DB 热配置）覆盖 env。"""

import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)


def _header_text(value, limit: int) -> str:
    """邮件头必须是单行。

    `EmailMessage.__setitem__` 对含 \\r/\\n 的值直接 raise ValueError，而这行赋值在
    `send_email` 的 try 之外——规则名、广播主题都是管理端自由文本，一个换行就能让
    异常一路冒到 run_collection，把整轮采集判 FAILED、规则引擎与后续聚合全部中止
    （SEC-020）。在这里统一折成空格，比让调用方各自记得过滤可靠。
    """
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:limit]


async def send_email(
    to: str,
    subject: str,
    body: str,
    smtp: dict | None = None,
    *,
    html_body: str | None = None,
) -> bool:
    """发送邮件；smtp 参数缺省时用 env 配置。返回是否真实发信（False=走落库通道）。"""
    to = _header_text(to, 254)
    subject = _header_text(subject, 255)
    config = smtp or _env_smtp()
    if not config.get("host"):
        logger.info("SMTP not configured; email to %s not sent (subject=%s)", to, subject)
        return False
    import aiosmtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = _header_text(config.get("from"), 254) or "AniSignal <noreply@anisignal.local>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    port = int(config.get("port") or 587)
    tls_mode = str(config.get("tls_mode") or "").lower()
    if tls_mode not in {"starttls", "tls", "none"}:
        tls_mode = "tls" if port == 465 else (
            "starttls" if bool(config.get("starttls", True)) else "none"
        )
    try:
        await aiosmtplib.send(
            message,
            hostname=config["host"],
            port=port,
            username=config.get("user") or None,
            password=config.get("password") or None,
            use_tls=tls_mode == "tls",
            start_tls=tls_mode == "starttls",
        )
        logger.info("email sent to %s (subject=%s)", to, subject)
        return True
    except Exception as exc:  # noqa: BLE001 - 发信失败不阻断流程，验证码走落库通道
        logger.warning("email to %s failed: %r", to, exc)
        return False


def _env_smtp() -> dict:
    settings = get_settings()
    tls_mode = settings.smtp_tls_mode.lower()
    if tls_mode not in {"starttls", "tls", "none"}:
        tls_mode = "tls" if settings.smtp_port == 465 else (
            "starttls" if settings.smtp_starttls else "none"
        )
    return {
        "host": settings.smtp_host,
        "port": settings.smtp_port,
        "user": settings.smtp_user,
        "password": settings.smtp_password,
        "from": settings.smtp_from,
        "tls_mode": tls_mode,
    }
