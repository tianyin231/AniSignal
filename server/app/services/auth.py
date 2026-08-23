"""账号安全服务：scrypt 密码、服务端会话、邮箱验证码、进程内限频。

限频说明：api 容器单 uvicorn worker，进程内字典即全局生效；
扩多 worker 时需换成 Redis 计数。
"""

import hashlib
import logging
import re
import secrets
import time
from collections import deque
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import EmailCode, User, UserSession
from app.services.mailer import send_email

logger = logging.getLogger(__name__)

SESSION_COOKIE = "anisignal_session"
_CODE_TTL_MINUTES = 15
_CODE_MAX_ATTEMPTS = 5
_SEND_MIN_INTERVAL = 60  # 同邮箱两次发码最小间隔（秒）
_SEND_HOURLY_LIMIT = 5
_LOGIN_FAIL_WINDOW = 15 * 60
_LOGIN_FAIL_LIMIT = 5

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 进程内限频状态（单 worker 有效）
_send_times: dict[str, deque[float]] = {}
_login_failures: dict[str, deque[float]] = {}


class AuthError(Exception):
    """业务错误（带对用户安全的提示文案）。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ---------- 密码 ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(bytes.fromhex(hash_hex)),
        )
        return secrets.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def validate_credentials(email: str, password: str) -> tuple[str, str]:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 255:
        raise AuthError("邮箱格式不正确")
    if len(password) < 8 or len(password) > 128:
        raise AuthError("密码至少 8 位")
    return email, password


# ---------- 验证码 ----------

def _check_send_limit(email: str) -> None:
    now = time.monotonic()
    times = _send_times.setdefault(email, deque())
    while times and now - times[0] > 3600:
        times.popleft()
    if times and now - times[-1] < _SEND_MIN_INTERVAL:
        raise AuthError("发送太频繁，请 1 分钟后再试", 429)
    if len(times) >= _SEND_HOURLY_LIMIT:
        raise AuthError("发送次数过多，请 1 小时后再试", 429)
    times.append(now)


async def issue_code(session: AsyncSession, user: User, purpose: str) -> bool:
    """签发验证码并发送；返回是否真实发信（False=走落库通道）。"""
    _check_send_limit(user.email)
    # 旧未用码全部作废
    await session.execute(
        update(EmailCode)
        .where(EmailCode.user_id == user.id, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
        .values(used_at=utcnow())
    )
    code = f"{secrets.randbelow(1000000):06d}"
    row = EmailCode(
        user_id=user.id,
        purpose=purpose,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        expires_at=utcnow() + timedelta(minutes=_CODE_TTL_MINUTES),
    )
    session.add(row)
    await session.flush()

    action = "注册验证" if purpose == "register" else "密码重置"
    from app.services.settings import get_effective_smtp

    sent = await send_email(
        user.email,
        f"AniSignal {action}验证码",
        f"你的验证码是：{code}\n{action}码 {_CODE_TTL_MINUTES} 分钟内有效。\n"
        f"如果这不是你本人的操作，请忽略本邮件。",
        smtp=await get_effective_smtp(session),
    )
    if not sent:
        row.code_plain = code  # SMTP 未配置/失败：管理端排查通道
        logger.info("verification code for %s retained in db (smtp unavailable)", user.email)
    return sent


async def verify_code(session: AsyncSession, user: User, purpose: str, code: str) -> None:
    row = await session.scalar(
        select(EmailCode)
        .where(
            EmailCode.user_id == user.id,
            EmailCode.purpose == purpose,
            EmailCode.used_at.is_(None),
        )
        .order_by(EmailCode.id.desc())
        .limit(1)
    )
    if row is None:
        raise AuthError("验证码不存在或已使用，请重新获取")
    if row.expires_at <= utcnow():
        raise AuthError("验证码已过期，请重新获取")
    if row.attempts >= _CODE_MAX_ATTEMPTS:
        raise AuthError("尝试次数过多，请重新获取验证码")
    if not secrets.compare_digest(
        hashlib.sha256(code.strip().encode()).hexdigest(), row.code_hash
    ):
        row.attempts += 1
        await session.commit()
        raise AuthError("验证码不正确")
    row.used_at = utcnow()


# ---------- 会话 ----------

async def create_session(session: AsyncSession, user: User) -> str:
    from app.config import get_settings

    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(days=get_settings().session_days),
        )
    )
    await session.commit()
    return token


async def get_user_by_token(session: AsyncSession, token: str) -> User | None:
    row = await session.scalar(
        select(UserSession).where(
            UserSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if row is None or row.expires_at <= utcnow():
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    # 滑动续期
    row.last_seen_at = utcnow()
    from app.config import get_settings

    row.expires_at = utcnow() + timedelta(days=get_settings().session_days)
    await session.commit()
    return user


async def revoke_current_session(session: AsyncSession, token: str) -> None:
    await session.execute(
        delete(UserSession).where(
            UserSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    await session.commit()


async def revoke_all_sessions(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(UserSession).where(UserSession.user_id == user_id))
    await session.commit()


# ---------- 登录防爆破 ----------

def _check_login_lock(email: str) -> None:
    now = time.monotonic()
    fails = _login_failures.setdefault(email, deque())
    while fails and now - fails[0] > _LOGIN_FAIL_WINDOW:
        fails.popleft()
    if len(fails) >= _LOGIN_FAIL_LIMIT:
        raise AuthError("失败次数过多，请 15 分钟后再试", 429)


def record_login_failure(email: str) -> None:
    _login_failures.setdefault(email, deque()).append(time.monotonic())


def clear_login_failures(email: str) -> None:
    _login_failures.pop(email, None)
