"""邮件发送：通用 SMTP（aiosmtplib）。未配置主机时返回 False（调用方走落库通道）。"""

import logging

from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("SMTP not configured; email to %s not sent (subject=%s)", to, subject)
        return False
    import aiosmtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
        )
        logger.info("email sent to %s (subject=%s)", to, subject)
        return True
    except Exception as exc:  # noqa: BLE001 - 发信失败不阻断流程，验证码走落库通道
        logger.warning("email to %s failed: %r", to, exc)
        return False
