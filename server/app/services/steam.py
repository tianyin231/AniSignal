"""Steam 游戏级遥测服务：快照落库、公开趋势聚合与数据抽稀。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.communities import COMMUNITIES, is_all_community
from app.models import CollectTarget, SteamAppSnapshot
from app.parsers.steam import store_url

_MIN_SNAPSHOT_SPACING = timedelta(minutes=5)
_MAX_SERIES_POINTS = 120
STEAM_FULL_DAYS = 30
_BATCH_SIZE = 1000

SCORE_LABELS: dict[str, str] = {
    "Overwhelmingly Positive": "好评如潮",
    "Very Positive": "特别好评",
    "Positive": "好评",
    "Mostly Positive": "多半好评",
    "Mixed": "褒贬不一",
    "Mostly Negative": "多半差评",
    "Negative": "差评",
    "Very Negative": "特别差评",
    "Overwhelmingly Negative": "差评如潮",
    "No user reviews": "暂无评测",
}


def _score_label(desc: str | None) -> str | None:
    if not desc:
        return None
    if desc in SCORE_LABELS:
        return SCORE_LABELS[desc]
    if desc.endswith(" user reviews") or desc.endswith(" user review"):
        return "评测较少"
    return desc


def _rate(pos: int | None, neg: int | None) -> float | None:
    if pos is None or neg is None:
        return None
    total = pos + neg
    if total <= 0:
        return None
    return round((pos / total) * 100.0, 1)


def _downsample(points: list[dict], limit: int = _MAX_SERIES_POINTS) -> list[dict]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    indices = sorted({round(i * step) for i in range(limit - 1)} | {len(points) - 1})
    return [points[i] for i in indices]


def _build_app_pulse(
    app_id: int,
    snaps: list[SteamAppSnapshot],
    since_24h: datetime,
    *,
    community: str | None = None,
    fallback_name: str | None = None,
) -> dict:
    latest = snaps[-1]
    ccu_points = [
        {"ts": s.captured_at.isoformat() + "Z", "player_count": s.player_count}
        for s in snaps
        if s.player_count is not None
    ]
    peak_window = max((p["player_count"] for p in ccu_points), default=None)
    ccu_24h = [s.player_count for s in snaps if s.player_count is not None and s.captured_at >= since_24h]
    peak_24h = max(ccu_24h) if ccu_24h else peak_window
    score_desc = latest.global_score_desc
    return {
        "app_id": app_id,
        "community": community,
        "name": latest.name or fallback_name or f"Steam #{app_id}",
        "store_url": store_url(app_id),
        "player_count": latest.player_count,
        "peak_24h": peak_24h,
        "peak_window": peak_window,
        "global_positive": latest.global_positive,
        "global_negative": latest.global_negative,
        "global_rate": _rate(latest.global_positive, latest.global_negative),
        "global_score_desc": score_desc,
        "global_score_label": _score_label(score_desc),
        "cn_positive": latest.cn_positive,
        "cn_negative": latest.cn_negative,
        "cn_rate": _rate(latest.cn_positive, latest.cn_negative),
        "price_formatted": latest.price_formatted,
        "discount_percent": latest.discount_percent,
        "captured_at": latest.captured_at.isoformat() + "Z",
        "series": _downsample(ccu_points),
    }


async def save_steam_snapshot(
    session: AsyncSession,
    community: str,
    snapshot: dict,
    *,
    now: datetime | None = None,
) -> SteamAppSnapshot | None:
    """落库一条 Steam 遥测快照；5 分钟内同游戏重复触发不产生冗余点。"""
    app_id = int(snapshot["app_id"])
    ts = now or utcnow()
    latest = await session.scalar(
        select(SteamAppSnapshot)
        .where(SteamAppSnapshot.community == community, SteamAppSnapshot.app_id == app_id)
        .order_by(SteamAppSnapshot.captured_at.desc(), SteamAppSnapshot.id.desc())
        .limit(1)
    )
    if latest is not None and latest.captured_at > ts - _MIN_SNAPSHOT_SPACING:
        return latest

    row = SteamAppSnapshot(
        community=community,
        app_id=app_id,
        name=snapshot.get("name") or (latest.name if latest else None),
        player_count=snapshot.get("player_count"),
        global_positive=snapshot.get("global_positive"),
        global_negative=snapshot.get("global_negative"),
        global_score_desc=snapshot.get("global_score_desc"),
        cn_positive=snapshot.get("cn_positive"),
        cn_negative=snapshot.get("cn_negative"),
        price_formatted=snapshot.get("price_formatted") or (latest.price_formatted if latest else None),
        discount_percent=int(snapshot.get("discount_percent") or 0),
        captured_at=ts,
    )
    session.add(row)
    await session.flush()
    return row


async def get_steam_pulse(
    session: AsyncSession,
    community: str,
    hours: int = 168,
    *,
    exclude_communities: tuple[str, ...] | set[str] = (),
) -> dict:
    """返回指定分区（或全站聚合 all）的 Steam 在线人数与口碑脉冲；无数据时返回空数组。"""
    now = utcnow()
    since = now - timedelta(hours=hours)
    since_24h = now - timedelta(hours=24)

    if is_all_community(community):
        excluded = set(exclude_communities)
        active_games = [
            key
            for key, meta in COMMUNITIES.items()
            if meta.active and meta.domain == "game" and key not in excluded
        ]
        if not active_games:
            return {"community": community, "hours": hours, "apps": []}
        target_rows = list(await session.execute(
            select(CollectTarget.community, CollectTarget.target_value, CollectTarget.display_name)
            .where(
                CollectTarget.community.in_(active_games),
                CollectTarget.platform == "steam",
                CollectTarget.enabled.is_(True),
            )
            .order_by(CollectTarget.id.asc())
        ))
        target_names: dict[tuple[str, int], str] = {}
        ordered_pairs: list[tuple[str, int]] = []
        for comm, raw_val, disp in target_rows:
            if str(raw_val).isdigit():
                pair = (comm, int(raw_val))
                if pair not in ordered_pairs:
                    ordered_pairs.append(pair)
                if disp:
                    target_names[pair] = disp
        if not ordered_pairs:
            return {"community": community, "hours": hours, "apps": []}

        rows = list(await session.scalars(
            select(SteamAppSnapshot)
            .where(
                SteamAppSnapshot.community.in_({comm for comm, _ in ordered_pairs}),
                SteamAppSnapshot.captured_at >= since,
            )
            .order_by(SteamAppSnapshot.captured_at.asc(), SteamAppSnapshot.id.asc())
        ))
        by_pair: dict[tuple[str, int], list[SteamAppSnapshot]] = {}
        pair_set = set(ordered_pairs)
        for row in rows:
            pair = (row.community, row.app_id)
            if pair in pair_set:
                by_pair.setdefault(pair, []).append(row)

        for comm, aid in ordered_pairs:
            if (comm, aid) not in by_pair:
                last_row = await session.scalar(
                    select(SteamAppSnapshot)
                    .where(SteamAppSnapshot.community == comm, SteamAppSnapshot.app_id == aid)
                    .order_by(SteamAppSnapshot.captured_at.desc(), SteamAppSnapshot.id.desc())
                    .limit(1)
                )
                if last_row is not None:
                    by_pair[(comm, aid)] = [last_row]

        apps = [
            _build_app_pulse(
                aid,
                by_pair[(comm, aid)],
                since_24h,
                community=comm,
                fallback_name=target_names.get((comm, aid)),
            )
            for comm, aid in ordered_pairs
            if (comm, aid) in by_pair
        ]
        apps.sort(key=lambda item: (item["player_count"] is None, -(item["player_count"] or 0), item["community"] or ""))
        return {"community": community, "hours": hours, "apps": apps}

    # 单分区视角：若分区配置了启用的 Steam 目标，优先按这些 AppID 顺序展示；否则回退到历史有快照的 AppID。
    target_rows = list(await session.execute(
        select(CollectTarget.target_value, CollectTarget.display_name)
        .where(
            CollectTarget.community == community,
            CollectTarget.platform == "steam",
            CollectTarget.enabled.is_(True),
        )
        .order_by(CollectTarget.id.asc())
    ))
    target_names_single: dict[int, str] = {}
    ordered_ids: list[int] = []
    for raw_val, disp in target_rows:
        if str(raw_val).isdigit():
            aid = int(raw_val)
            if aid not in ordered_ids:
                ordered_ids.append(aid)
            if disp:
                target_names_single[aid] = disp

    rows = list(await session.scalars(
        select(SteamAppSnapshot)
        .where(
            SteamAppSnapshot.community == community,
            SteamAppSnapshot.captured_at >= since,
        )
        .order_by(SteamAppSnapshot.captured_at.asc(), SteamAppSnapshot.id.asc())
    ))
    by_app: dict[int, list[SteamAppSnapshot]] = {}
    for row in rows:
        by_app.setdefault(row.app_id, []).append(row)

    missing_ids = [aid for aid in ordered_ids if aid not in by_app]
    for aid in missing_ids:
        last_row = await session.scalar(
            select(SteamAppSnapshot)
            .where(SteamAppSnapshot.community == community, SteamAppSnapshot.app_id == aid)
            .order_by(SteamAppSnapshot.captured_at.desc(), SteamAppSnapshot.id.desc())
            .limit(1)
        )
        if last_row is not None:
            by_app[aid] = [last_row]

    app_ids = [aid for aid in ordered_ids if aid in by_app] + [aid for aid in by_app if aid not in ordered_ids]
    apps = [
        _build_app_pulse(
            aid,
            by_app[aid],
            since_24h,
            community=community,
            fallback_name=target_names_single.get(aid),
        )
        for aid in app_ids
    ]
    return {"community": community, "hours": hours, "apps": apps}


async def get_steam_app_pulse(session: AsyncSession, app_id: int, hours: int = 168) -> dict | None:
    """按 AppID 返回单款游戏的实时遥测（供定制监控详情页复用，优先连续点最多的记录序列）。"""
    now = utcnow()
    since = now - timedelta(hours=hours)
    since_24h = now - timedelta(hours=24)

    rows = list(await session.scalars(
        select(SteamAppSnapshot)
        .where(SteamAppSnapshot.app_id == app_id, SteamAppSnapshot.captured_at >= since)
        .order_by(SteamAppSnapshot.captured_at.asc(), SteamAppSnapshot.id.asc())
    ))
    if not rows:
        last_row = await session.scalar(
            select(SteamAppSnapshot)
            .where(SteamAppSnapshot.app_id == app_id)
            .order_by(SteamAppSnapshot.captured_at.desc(), SteamAppSnapshot.id.desc())
            .limit(1)
        )
        if last_row is None:
            return None
        rows = [last_row]

    by_comm: dict[str, list[SteamAppSnapshot]] = {}
    for row in rows:
        by_comm.setdefault(row.community, []).append(row)
    best_comm = max(
        by_comm,
        key=lambda comm: (len(by_comm[comm]), comm != "custom", by_comm[comm][-1].captured_at),
    )
    return _build_app_pulse(
        app_id,
        by_comm[best_comm],
        since_24h,
        community=None if best_comm == "custom" else best_comm,
    )


async def thin_steam_snapshots(session: AsyncSession, *, now: datetime | None = None) -> int:
    """超过保留期的 Steam 快照每款游戏每天只留最后一条，返回删除行数。"""
    cutoff = (now or utcnow()) - timedelta(days=STEAM_FULL_DAYS)
    keep = set(await session.scalars(
        select(func.max(SteamAppSnapshot.id))
        .where(SteamAppSnapshot.captured_at < cutoff)
        .group_by(SteamAppSnapshot.community, SteamAppSnapshot.app_id, func.date(SteamAppSnapshot.captured_at))
    ))
    removed = 0
    cursor = 0
    while True:
        ids = list(await session.scalars(
            select(SteamAppSnapshot.id)
            .where(SteamAppSnapshot.id > cursor, SteamAppSnapshot.captured_at < cutoff)
            .order_by(SteamAppSnapshot.id)
            .limit(_BATCH_SIZE)
        ))
        if not ids:
            return removed
        cursor = ids[-1]
        stale = [sid for sid in ids if sid not in keep]
        if stale:
            await session.execute(delete(SteamAppSnapshot).where(SteamAppSnapshot.id.in_(stale)))
        await session.commit()
        removed += len(stale)
