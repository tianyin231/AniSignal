"""GitHub Trending 分期刊物归档、跨期去重与上榜打标服务（日刊 / 周刊 / 月刊）。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import ContentVersion, GitTrendingEdition, RawContent

BEIJING_TZ = timezone(timedelta(hours=8))
TRENDING_PERIOD_ORDER = ("daily", "weekly", "monthly")
TRENDING_PERIOD_LABELS = {
    "daily": "日刊",
    "weekly": "周刊",
    "monthly": "月刊",
}


def trending_period_key(period: str, now: datetime | None = None) -> str:
    """按北京时间定界生成期次键：daily=YYYY-MM-DD, weekly=YYYY-Www, monthly=YYYY-MM。"""
    ts = now or utcnow()
    utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    bj = utc.astimezone(BEIJING_TZ)
    if period == "daily":
        return f"{bj.year:04d}-{bj.month:02d}-{bj.day:02d}"
    if period == "weekly":
        iso_year, iso_week, _ = bj.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    if period == "monthly":
        return f"{bj.year:04d}-{bj.month:02d}"
    raise ValueError(f"unsupported trending period: {period}")


def _repo_key(entry: dict) -> str:
    return str(entry.get("repo") or "").strip().lower()


async def save_trending_edition(
    session: AsyncSession,
    *,
    community: str,
    period: str,
    language: str = "",
    entries: list[dict],
    repo_to_content_id: dict[str, int] | None = None,
    now: datetime | None = None,
) -> GitTrendingEdition:
    """记录或原地刷新当期 Trending 刊物快照，并计算本期首次上榜数量。"""
    ts = now or utcnow()
    period_key = trending_period_key(period, ts)
    lang = str(language or "").strip().lower()
    content_map = {k.lower(): v for k, v in (repo_to_content_id or {}).items()}

    normalized_entries: list[dict[str, Any]] = []
    seen_repos: set[str] = set()
    for idx, raw in enumerate(entries, start=1):
        repo = str(raw.get("repo") or "").strip()
        rkey = repo.lower()
        if not repo or rkey in seen_repos:
            continue
        seen_repos.add(rkey)
        normalized_entries.append(
            {
                "rank": int(raw.get("rank") or idx),
                "repo": repo,
                "url": str(raw.get("url") or f"https://github.com/{repo}"),
                "description": str(raw.get("description") or "").strip(),
                "language": str(raw.get("language") or "").strip(),
                "stars": int(raw.get("stars") or 0),
                "forks": int(raw.get("forks") or 0),
                "period_stars": int(raw.get("period_stars") or 0),
                "raw_content_id": content_map.get(rkey) or raw.get("raw_content_id"),
            }
        )

    prior_rows = list(
        await session.scalars(
            select(GitTrendingEdition).where(
                GitTrendingEdition.community == community,
                GitTrendingEdition.period == period,
                GitTrendingEdition.language == lang,
                GitTrendingEdition.period_key < period_key,
            )
        )
    )
    prior_repos: set[str] = set()
    for row in prior_rows:
        for item in row.entries or []:
            rk = _repo_key(item)
            if rk:
                prior_repos.add(rk)

    new_count = sum(1 for item in normalized_entries if _repo_key(item) not in prior_repos)

    existing = await session.scalar(
        select(GitTrendingEdition).where(
            GitTrendingEdition.community == community,
            GitTrendingEdition.period == period,
            GitTrendingEdition.period_key == period_key,
            GitTrendingEdition.language == lang,
        )
    )
    if existing is None:
        edition = GitTrendingEdition(
            community=community,
            period=period,
            period_key=period_key,
            language=lang,
            entries=normalized_entries,
            item_count=len(normalized_entries),
            new_count=new_count,
            captured_at=ts,
            created_at=ts,
        )
        session.add(edition)
        await session.flush()
        return edition

    existing.entries = normalized_entries
    existing.item_count = len(normalized_entries)
    existing.new_count = new_count
    existing.captured_at = ts
    await session.flush()
    return existing


def annotate_edition(
    editions_asc: list[GitTrendingEdition],
    target_index: int,
    *,
    other_period_repos: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """根据同周期历史刊物，为目标期的每个仓库计算跨期去重状态、连榜期数、历史最高排名与跨榜标记。"""
    target = editions_asc[target_index]
    first_seen: dict[str, str] = {}
    appearances: Counter[str] = Counter()
    best_rank: dict[str, int] = {}

    for ed in editions_asc[: target_index + 1]:
        for idx, item in enumerate(ed.entries or [], start=1):
            rk = _repo_key(item)
            if not rk:
                continue
            rank = int(item.get("rank") or idx)
            if rk not in first_seen:
                first_seen[rk] = ed.period_key
            appearances[rk] += 1
            best_rank[rk] = min(best_rank.get(rk, rank), rank)

    prev_ranks: dict[str, int] = {}
    if target_index > 0:
        for idx, item in enumerate(editions_asc[target_index - 1].entries or [], start=1):
            rk = _repo_key(item)
            if rk:
                prev_ranks[rk] = int(item.get("rank") or idx)

    # 反向统计每条仓库截至本期的连续上榜期数
    period_sets = [
        {_repo_key(item) for item in (ed.entries or []) if _repo_key(item)}
        for ed in editions_asc[: target_index + 1]
    ]

    def _streak(rk: str) -> int:
        count = 0
        for idx in range(target_index, -1, -1):
            if rk in period_sets[idx]:
                count += 1
            else:
                break
        return count

    other_map = other_period_repos or {}
    annotated_entries: list[dict[str, Any]] = []
    lang_counter: Counter[str] = Counter()
    new_total = 0
    streak_total = 0
    returning_total = 0

    for idx, item in enumerate(target.entries or [], start=1):
        rk = _repo_key(item)
        rank = int(item.get("rank") or idx)
        seen_times = appearances.get(rk, 1)
        prev_rank = prev_ranks.get(rk)
        streak = _streak(rk)
        if seen_times <= 1:
            status = "new"
            new_total += 1
        elif prev_rank is not None:
            status = "streak"
            streak_total += 1
        else:
            status = "returning"
            returning_total += 1

        rank_delta = (prev_rank - rank) if prev_rank is not None else None
        also_in = [p for p in TRENDING_PERIOD_ORDER if p != target.period and rk in other_map.get(p, set())]
        lang = str(item.get("language") or "").strip()
        if lang:
            lang_counter[lang] += 1

        annotated_entries.append(
            {
                "rank": rank,
                "repo": item.get("repo") or "",
                "url": item.get("url") or "",
                "description": item.get("description") or "",
                "language": lang,
                "stars": int(item.get("stars") or 0),
                "forks": int(item.get("forks") or 0),
                "period_stars": int(item.get("period_stars") or 0),
                "raw_content_id": item.get("raw_content_id"),
                "status": status,  # new | streak | returning
                "streak_count": streak,
                "appearances": seen_times,
                "first_seen_key": first_seen.get(rk, target.period_key),
                "best_rank": best_rank.get(rk, rank),
                "rank_delta": rank_delta,
                "also_in": also_in,
            }
        )

    languages = [
        {"name": name, "count": count}
        for name, count in sorted(lang_counter.items(), key=lambda pair: (-pair[1], pair[0].lower()))
    ]

    return {
        "period": target.period,
        "period_label": TRENDING_PERIOD_LABELS.get(target.period, target.period),
        "period_key": target.period_key,
        "issue_number": target_index + 1,
        "captured_at": target.captured_at,
        "prev_key": editions_asc[target_index - 1].period_key if target_index > 0 else None,
        "next_key": editions_asc[target_index + 1].period_key if target_index + 1 < len(editions_asc) else None,
        "stats": {
            "total": len(annotated_entries),
            "new_count": new_total,
            "streak_count": streak_total,
            "returning_count": returning_total,
        },
        "languages": languages,
        "entries": annotated_entries,
    }


async def get_trending_editions_view(
    session: AsyncSession,
    *,
    community: str,
    period: str | None = None,
    period_key: str | None = None,
    language: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """返回指定分区的 Trending 刊物总览、历史目录、当期/指定期已打标榜单与主仓库最新发版。"""
    lang = str(language or "").strip().lower()
    all_rows = list(
        await session.scalars(
            select(GitTrendingEdition)
            .where(
                GitTrendingEdition.community == community,
                GitTrendingEdition.language == lang,
            )
            .order_by(GitTrendingEdition.period_key.asc())
        )
    )

    by_period: dict[str, list[GitTrendingEdition]] = {p: [] for p in TRENDING_PERIOD_ORDER}
    for row in all_rows:
        if row.period in by_period:
            by_period[row.period].append(row)

    periods_meta = []
    other_latest_repos: dict[str, set[str]] = {}
    for p in TRENDING_PERIOD_ORDER:
        rows = by_period[p]
        latest = rows[-1] if rows else None
        other_latest_repos[p] = (
            {_repo_key(item) for item in (latest.entries or []) if _repo_key(item)}
            if latest is not None
            else set()
        )
        periods_meta.append(
            {
                "period": p,
                "label": TRENDING_PERIOD_LABELS[p],
                "edition_count": len(rows),
                "latest_key": latest.period_key if latest else None,
                "latest_captured_at": latest.captured_at if latest else None,
            }
        )

    has_editions = any(by_period[p] for p in TRENDING_PERIOD_ORDER)
    if period in by_period:
        active_period = period
    else:
        active_period = next((p for p in TRENDING_PERIOD_ORDER if by_period[p]), "daily")

    active_rows = by_period.get(active_period, [])
    editions_index = [
        {
            "period_key": row.period_key,
            "issue_number": idx + 1,
            "item_count": row.item_count,
            "new_count": row.new_count,
            "captured_at": row.captured_at,
        }
        for idx, row in enumerate(active_rows)
    ]
    editions_index.reverse()
    editions_index = editions_index[:limit]

    current = None
    if active_rows:
        target_idx = len(active_rows) - 1
        if period_key:
            matched = next((i for i, r in enumerate(active_rows) if r.period_key == period_key), None)
            if matched is not None:
                target_idx = matched
        current = annotate_edition(
            active_rows,
            target_idx,
            other_period_repos=other_latest_repos,
        )

    # 顺带读取同分区最新的主仓库 Release，供刊物边栏展示
    release_rows = (
        await session.execute(
            select(RawContent, ContentVersion)
            .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
            .where(
                RawContent.community == community,
                RawContent.content_type == "release",
                RawContent.hidden.is_(False),
            )
            .order_by(desc(RawContent.published_at), desc(RawContent.id))
            .limit(4)
        )
    ).all()
    releases = [
        {
            "id": raw.id,
            "title": ver.title or raw.external_id,
            "url": raw.url,
            "published_at": raw.published_at,
            "tag_name": ((ver.normalized_json or {}).get("extra") or {}).get("tag_name"),
            "repo": ((ver.normalized_json or {}).get("extra") or {}).get("repo"),
            "prerelease": bool(((ver.normalized_json or {}).get("extra") or {}).get("prerelease")),
        }
        for raw, ver in release_rows
    ]

    return {
        "community": community,
        "has_editions": has_editions,
        "active_period": active_period,
        "periods": periods_meta,
        "editions": editions_index,
        "current": current,
        "releases": releases,
    }
