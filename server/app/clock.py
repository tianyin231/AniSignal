"""时间约定：数据库一律存 naive UTC，边界处自行转换时区。"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

BJ_TZ = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    """当前 UTC 时间（naive，用于数据库列）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """把 aware datetime 规整为 naive UTC；naive 输入视为已是 UTC。"""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def to_beijing(value: datetime) -> datetime:
    """将时间转换为北京时间（aware UTC+8）；naive 输入视为 UTC。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BJ_TZ)


def now_beijing() -> datetime:
    """当前北京时间（aware UTC+8）。"""
    return datetime.now(BJ_TZ)


def format_beijing(value: datetime, fmt: str = "%Y/%m/%d %H:%M") -> str:
    return (to_naive_utc(value) + timedelta(hours=8)).strftime(fmt)


def feed_time(value: str) -> datetime | None:
    """解析 RSS / Atom / ISO-8601 / RFC-2822 时间字符串为 aware UTC datetime。"""
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
