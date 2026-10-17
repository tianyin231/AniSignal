"""信源分级：按粉丝量给来源分档，通知里标注权重，高权重命中优先展示。

思路借鉴 Brand24 的"来源权重"概念（自写实现）：粉丝量阈值可配置（默认 10w / 100w
两档），档位写在 sources.tier 上，求值与排序都不再实时算粉丝数。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source

TIER_HIGH = "high"
TIER_MID = "mid"
TIER_LOW = "low"
TIER_UNKNOWN = "unknown"

# 档位 → 排序权重（越大越优先）
TIER_WEIGHTS = {TIER_HIGH: 3, TIER_MID: 2, TIER_LOW: 1, TIER_UNKNOWN: 0}

TIER_LABELS = {
    TIER_HIGH: "高权重",
    TIER_MID: "中权重",
    TIER_LOW: "低权重",
    TIER_UNKNOWN: "未分级",
}

# 默认阈值：10w 起算中权重，100w 以上算高权重
DEFAULT_THRESHOLDS = {"mid": 100_000, "high": 1_000_000}


def normalize_thresholds(raw: dict | None) -> dict[str, int]:
    """清洗配置：只保留两个正向阈值，并保证 mid <= high。"""
    merged = dict(DEFAULT_THRESHOLDS)
    for key in ("mid", "high"):
        value = (raw or {}).get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            merged[key] = number
    if merged["mid"] > merged["high"]:
        merged["mid"] = merged["high"]
    return merged


def classify(follower_count: int | None, thresholds: dict | None = None) -> str:
    """粉丝量 → 档位；没有粉丝数据时是 unknown（不猜）。"""
    limits = normalize_thresholds(thresholds)
    if not follower_count or follower_count <= 0:
        return TIER_UNKNOWN
    if follower_count >= limits["high"]:
        return TIER_HIGH
    if follower_count >= limits["mid"]:
        return TIER_MID
    return TIER_LOW


def weight(tier: str | None) -> int:
    return TIER_WEIGHTS.get(tier or TIER_UNKNOWN, 0)


def label(tier: str | None) -> str:
    return TIER_LABELS.get(tier or TIER_UNKNOWN, TIER_LABELS[TIER_UNKNOWN])


async def refresh_source_tiers(
    session: AsyncSession, source_ids: list[int], thresholds: dict | None = None
) -> dict[int, str]:
    """按当前粉丝量与阈值重算来源档位，返回 source_id → tier（只返回有变化的）。"""
    ids = [int(value) for value in set(source_ids or []) if value]
    if not ids:
        return {}
    rows = (
        await session.execute(select(Source).where(Source.id.in_(ids)))
    ).scalars()
    changed: dict[int, str] = {}
    for source in rows:
        tier = classify(source.follower_count, thresholds)
        if source.tier != tier:
            source.tier = tier
            changed[source.id] = tier
    if changed:
        await session.commit()
    return changed


async def tiers_for_contents(
    session: AsyncSession, content_ids: list[int]
) -> dict[int, tuple[str, str | None]]:
    """content_id → (tier, 来源名)；没有来源时是 unknown。"""
    from app.models import RawContent

    ids = [int(value) for value in set(content_ids or []) if value]
    if not ids:
        return {}
    rows = await session.execute(
        select(RawContent.id, Source.tier, Source.name)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(RawContent.id.in_(ids))
    )
    result: dict[int, tuple[str, str | None]] = {}
    for content_id, tier, name in rows:
        result[content_id] = (tier or TIER_UNKNOWN, name)
    return result
