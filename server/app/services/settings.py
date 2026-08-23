"""站点配置服务：KV 读写 + SMTP 热配置合成。

文案区域使用白名单 key（前端组件按 key 读取并带默认值兜底）。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import SiteSetting

logger = logging.getLogger(__name__)

# 可配置文案区域（key → 默认值）；前端 useSiteConfig 用同一组 key
DEFAULT_TEXTS: dict[str, str] = {
    "footer_note": "AniSignal · 鸣潮事件雷达 · 不承诺全网覆盖",
    "home_intro": "多平台官方内容自动归并为带证据链的事件；全站免费浏览，登录用于订阅与通知。",
    "official_note": "已接入官方来源的原始内容流；跨平台重复公告的合并视图将在事件引擎上线后提供。",
    "events_note": "事件由引擎自动聚合、人工复核修正；每条结论可回溯原始证据。",
    "sources_note": "本页公开展示已接入来源、最近成功采集时间与不可用状态。全站任何「未发现」均指「在已接入来源中未发现」，不宣传全网覆盖。",
    "watch_intro": "邮件通知、早报与监控位正在开发中。上线前，本站全部信息免费开放，无需注册。",
}

DEFAULT_ANNOUNCEMENT = {
    "enabled": False,
    "level": "info",  # info / warning
    "text": "",
    "link": "",
}


async def get_setting(session: AsyncSession, key: str) -> dict | None:
    row = await session.get(SiteSetting, key)
    return row.value if row else None


async def set_setting(session: AsyncSession, key: str, value: dict) -> None:
    row = await session.get(SiteSetting, key)
    if row is None:
        session.add(SiteSetting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def get_texts(session: AsyncSession) -> dict[str, str]:
    stored = await get_setting(session, "texts") or {}
    return {**DEFAULT_TEXTS, **{k: v for k, v in stored.items() if k in DEFAULT_TEXTS}}


async def get_announcement(session: AsyncSession) -> dict:
    stored = await get_setting(session, "announcement") or {}
    merged = {**DEFAULT_ANNOUNCEMENT, **stored}
    return merged if merged.get("enabled") and merged.get("text") else {**merged, "enabled": False}


async def get_effective_smtp(session: AsyncSession) -> dict:
    """DB 配置优先，回落 env。返回字段齐全的 SMTP 参数。"""
    env = get_settings()
    stored = await get_setting(session, "smtp") or {}
    return {
        "host": stored.get("host") or env.smtp_host,
        "port": int(stored.get("port") or env.smtp_port),
        "user": stored.get("user") if stored.get("user") is not None else env.smtp_user,
        "password": stored.get("password") or env.smtp_password,
        "from": stored.get("from") or env.smtp_from,
        "starttls": bool(stored.get("starttls", env.smtp_starttls)),
    }


def smtp_public_view(smtp: dict) -> dict:
    """管理端展示用：密码脱敏，只报是否已设置。"""
    return {
        "host": smtp["host"],
        "port": smtp["port"],
        "user": smtp["user"],
        "password_set": bool(smtp["password"]),
        "password_source": "db" if smtp["password"] and not get_settings().smtp_password else (
            "db" if smtp["password"] and smtp["password"] != get_settings().smtp_password else "env"
        ),
        "from": smtp["from"],
        "starttls": smtp["starttls"],
    }
