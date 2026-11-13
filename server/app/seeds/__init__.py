"""可选演示信源预设包：与数据库迁移解耦，支持按分区幂等导入。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from functools import lru_cache
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import get_community
from app.models import CollectTarget

_SEED_FILE = Path(__file__).with_name("demo_targets.json")


@lru_cache(maxsize=1)
def load_preset_packs() -> dict[str, list[dict]]:
    return json.loads(_SEED_FILE.read_text(encoding="utf-8"))


def preset_catalog() -> list[dict]:
    packs = load_preset_packs()
    items = []
    for community, targets in packs.items():
        meta = get_community(community)
        items.append({
            "community": community,
            "label": meta.label if meta.key == community else community,
            "domain": meta.domain if meta.key == community else "game",
            "count": len(targets),
            "enabled_count": sum(1 for t in targets if t.get("enabled", True)),
            "platforms": sorted({t["platform"] for t in targets}),
        })
    return items


async def import_preset_targets(
    session: AsyncSession,
    communities: Sequence[str] | None = None,
) -> dict:
    """按分区幂等导入预设采集目标，保留管理员已有配置与频率。"""
    packs = load_preset_packs()
    selected = [c for c in (communities if communities is not None else list(packs.keys())) if c in packs]
    if not selected:
        return {"added": 0, "skipped": 0, "communities": []}

    existing_rows = await session.execute(
        select(
            CollectTarget.platform,
            CollectTarget.target_type,
            CollectTarget.target_value,
            CollectTarget.community,
        ).where(CollectTarget.community.in_(selected))
    )
    existing_keys = {(r[0], r[1], r[2], r[3]) for r in existing_rows.all()}

    now = utcnow()
    added = 0
    skipped = 0
    for community in selected:
        for spec in packs[community]:
            key = (spec["platform"], spec["target_type"], spec["target_value"], community)
            if key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            enabled = bool(spec.get("enabled", True))
            session.add(
                CollectTarget(
                    community=community,
                    platform=spec["platform"],
                    target_type=spec["target_type"],
                    target_value=spec["target_value"],
                    display_name=spec.get("display_name"),
                    interval_minutes=int(spec.get("interval_minutes", 60)),
                    min_views=int(spec.get("min_views", 0)),
                    tier=spec.get("tier", "general"),
                    enabled=enabled,
                    co_terms=list(spec.get("co_terms") or []),
                    config={**dict(spec.get("config") or {}), "preset": True},
                    next_run_at=(now + timedelta(minutes=added % 15)) if enabled else None,
                )
            )
            added += 1

    if added:
        await session.commit()
    return {"added": added, "skipped": skipped, "communities": selected}
