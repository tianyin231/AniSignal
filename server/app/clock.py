"""时间约定：数据库一律存 naive UTC，边界处自行转换时区。"""

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """当前 UTC 时间（naive，用于数据库列）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """把 aware datetime 规整为 naive UTC；naive 输入视为已是 UTC。"""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def format_beijing(value: datetime, fmt: str = "%Y/%m/%d %H:%M") -> str:
    return (to_naive_utc(value) + timedelta(hours=8)).strftime(fmt)
