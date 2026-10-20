"""邮件发送：通用 SMTP（aiosmtplib），支持运行时（DB 热配置）覆盖 env。"""

import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)

# 邮件头里不允许出现换行与其它控制字符：`Subject`/`To`/`From` 都可能带上规则名、
# 站点名这类用户可控文本，含 `\r\n` 时 Python 的 EmailMessage 会直接抛 ValueError
# （不是静默注入，而是把整批发送打断）。除 C0/C1 控制符外，U+2028/U+2029/U+0085
# 这几个 Unicode 行分隔符同样会被 email 库拒收，一并折叠成空格。
_HEADER_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u0085]+")


def _header_text(value: object, limit: int = 255) -> str:
    """把任意文本收敛成单行邮件头值；超长截断（RFC 5322 一行不宜过长）。"""
    text = _HEADER_CONTROL_RE.sub(" ", str(value or ""))
    return " ".join(text.split())[:limit]


async def send_email(
    to: str,
    subject: str,
    body: str,
    smtp: dict | None = None,
    *,
    html_body: str | None = None,
) -> bool:
    """发送邮件；smtp 参数缺省时用 env 配置。返回是否真实发信（False=走落库通道）。"""
    config = smtp or _env_smtp()
    if not config.get("host"):
        logger.info("SMTP not configured; email to %s not sent (subject=%s)", to, subject)
        return False
    import aiosmtplib
    from email.message import EmailMessage

    to = _header_text(to, 254)
    subject = _header_text(subject, 255)
    port = int(config.get("port") or 587)
    tls_mode = str(config.get("tls_mode") or "").lower()
    if tls_mode not in {"starttls", "tls", "none"}:
        tls_mode = "tls" if port == 465 else (
            "starttls" if bool(config.get("starttls", True)) else "none"
        )
    try:
        message = EmailMessage()
        message["From"] = _header_text(config.get("from"), 254) or "AniSignal <noreply@anisignal.local>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
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
