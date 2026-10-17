"""站点配置服务：KV 读写 + SMTP / OnePush 热配置合成。

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
    "footer_note": "AniSignal · 定制化情报采集",
}

DEFAULT_ANNOUNCEMENT = {
    "enabled": False,
    "level": "info",  # info / warning
    "text": "",
    "link": "",
}

# 媒体可见性：按平台控制图片/视频展示（show=正常 / link=仅链接 / hide=不显示）
# default 为全站默认；platforms 覆盖单平台
MEDIA_STATES = {"show", "link", "hide"}
DEFAULT_MEDIA_VISIBILITY = {
    "default": {"image": "show", "video": "show"},
    "platforms": {},
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


def _normalize_media_visibility(stored: dict) -> dict:
    """合并默认值并校验状态枚举，非法键值丢弃。"""
    merged = {
        "default": {**DEFAULT_MEDIA_VISIBILITY["default"], **(stored.get("default") or {})},
        "platforms": dict(stored.get("platforms") or {}),
    }
    for key in ("image", "video"):
        if merged["default"].get(key) not in MEDIA_STATES:
            merged["default"][key] = "show"
    clean_platforms = {}
    for platform, modes in merged["platforms"].items():
        if not isinstance(modes, dict):
            continue
        clean = {k: v for k, v in modes.items() if k in ("image", "video") and v in MEDIA_STATES}
        if clean:
            clean_platforms[platform] = clean
    merged["platforms"] = clean_platforms
    return merged


def hidden_platforms(visibility: dict) -> list[str]:
    """图片与视频都设为 hide 的平台：公开内容列表整体排除。"""
    return sorted(
        platform
        for platform, modes in visibility.get("platforms", {}).items()
        if modes.get("image") == "hide" and modes.get("video") == "hide"
    )


async def get_media_visibility(session: AsyncSession) -> dict:
    stored = await get_setting(session, "media_visibility") or {}
    return _normalize_media_visibility(stored)


async def get_announcement(session: AsyncSession) -> dict:
    stored = await get_setting(session, "announcement") or {}
    merged = {**DEFAULT_ANNOUNCEMENT, **stored}
    return merged if merged.get("enabled") and merged.get("text") else {**merged, "enabled": False}


async def get_effective_smtp(session: AsyncSession) -> dict:
    """DB 配置优先，回落 env。返回字段齐全的 SMTP 参数。"""
    env = get_settings()
    stored = await get_setting(session, "smtp") or {}
    port = int(stored.get("port") or env.smtp_port)
    tls_mode = str(stored.get("tls_mode") or env.smtp_tls_mode).lower()
    if tls_mode not in {"starttls", "tls", "none"}:
        legacy_starttls = bool(stored.get("starttls", env.smtp_starttls))
        tls_mode = "tls" if port == 465 else ("starttls" if legacy_starttls else "none")
    return {
        "host": stored.get("host") or env.smtp_host,
        "port": port,
        "user": stored.get("user") if stored.get("user") is not None else env.smtp_user,
        "password": stored.get("password") or env.smtp_password,
        "from": stored.get("from") or env.smtp_from,
        "tls_mode": tls_mode,
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
        "tls_mode": smtp["tls_mode"],
    }


async def get_onepush(session: AsyncSession) -> dict:
    stored = await get_setting(session, "onepush") or {}
    config = stored.get("config") if isinstance(stored.get("config"), dict) else {}
    return {"enabled": bool(stored.get("enabled")), "config": config}


def onepush_public_view(onepush: dict) -> dict:
    """管理端展示用：只返回 provider 与字段名，不回显 token/key 等值。"""
    config = onepush.get("config") if isinstance(onepush.get("config"), dict) else {}
    provider = str(config.get("provider") or "")
    return {
        "enabled": bool(onepush.get("enabled")),
        "configured": bool(provider),
        "provider": provider,
        "fields": sorted(str(key) for key in config if key != "provider"),
    }


# ---------- 规则引擎与声量告警（批次 3a） ----------

# 声量异常判定与信源分级阈值（管理端可改，落 site_settings 的 "rules" 键）
DEFAULT_RULES_CONFIG = {
    # 规则引擎总开关
    "enabled": True,
    # 声量异常：新窗口命中数超过基线均值 N 倍即预警（默认 3 倍）
    "anomaly_multiplier": 3.0,
    # 统计窗口（分钟）与保留的历史窗口数（滑动基线样本）
    "window_minutes": 60,
    "history_windows": 24,
    # 基线样本不足或命中数太少时不告警，避免刚上线就刷屏
    "min_samples": 5,
    "min_hits": 5,
    # 同一分组两次预警的最小间隔（分钟）
    "cooldown_minutes": 120,
    # 信源分级阈值（粉丝量）：10w 起中权重，100w 以上高权重
    "source_tiers": {"mid": 100_000, "high": 1_000_000},
}


def _clamp_number(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _clamp_int(value, default: int, low: int, high: int) -> int:
    return int(_clamp_number(value, default, low, high))


def normalize_rules_config(stored: dict | None) -> dict:
    """合并默认值并夹紧数值：脏配置不能让告警失真或把循环打挂。"""
    merged = {**DEFAULT_RULES_CONFIG, **(stored or {})}
    return {
        "enabled": bool(merged.get("enabled", True)),
        "anomaly_multiplier": _clamp_number(merged.get("anomaly_multiplier"), 3.0, 1.5, 20.0),
        "window_minutes": _clamp_int(merged.get("window_minutes"), 60, 5, 24 * 60),
        "history_windows": _clamp_int(merged.get("history_windows"), 24, 3, 24 * 7),
        "min_samples": _clamp_int(merged.get("min_samples"), 5, 1, 200),
        "min_hits": _clamp_int(merged.get("min_hits"), 5, 1, 10_000),
        "cooldown_minutes": _clamp_int(merged.get("cooldown_minutes"), 120, 0, 24 * 60),
        "source_tiers": normalize_source_tiers(merged.get("source_tiers")),
    }


def normalize_source_tiers(raw: dict | None) -> dict[str, int]:
    """信源分级阈值（复用 source_tier 的清洗规则，避免两处口径）。"""
    from app.services.source_tier import normalize_thresholds

    return normalize_thresholds(raw if isinstance(raw, dict) else None)


async def get_rules_config(session: AsyncSession) -> dict:
    stored = await get_setting(session, "rules") or {}
    return normalize_rules_config(stored)
