"""统计预聚合写入与公开统计页查询。"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import (
    CollectRun,
    CollectTarget,
    CollectionDailyStat,
    ContentDailyStat,
)
from app.models.enums import RunStatus
from app.schemas.api import (
    StatisticsCollectionPoint,
    StatisticsIdentity,
    StatisticsKeyword,
    StatisticsOverview,
    StatisticsPlatform,
    StatisticsRecentRun,
    StatisticsTarget,
    StatisticsTotals,
    StatisticsTrendPoint,
)

BEIJING = timezone(timedelta(hours=8))


def beijing_date(value: datetime) -> date:
    return value.replace(tzinfo=timezone.utc).astimezone(BEIJING).date()


async def record_content_stat(
    session: AsyncSession,
    *,
    collected_at: datetime,
    platform: str,
    identity: str,
    keywords: list[str],
    include_base: bool,
) -> None:
    """记录新内容或新增关键词；与内容入库共享同一事务。"""
    day = beijing_date(collected_at)
    keys = {str(keyword).strip()[:255] for keyword in keywords if str(keyword).strip()}
    if include_base:
        keys.add("")
    if not keys:
        return
    now = utcnow()
    values = [dict(stat_date=day, platform=platform, identity=identity, keyword=keyword,
                   contents_new=1, updated_at=now) for keyword in sorted(keys)]
    # 多个账号同时首次入库时也必须能累加，不能先 SELECT 再 INSERT 或覆盖旧计数。
    if session.get_bind().dialect.name == "sqlite":
        statement = sqlite_insert(ContentDailyStat).values(values).on_conflict_do_update(
            index_elements=["stat_date", "platform", "identity", "keyword"],
            set_={"contents_new": ContentDailyStat.contents_new + 1, "updated_at": now})
    else:
        statement = mysql_insert(ContentDailyStat).values(values).on_duplicate_key_update(
            contents_new=ContentDailyStat.contents_new + 1, updated_at=now)
    await session.execute(statement)
    # 本事务曾读过的统计对象也要与数据库保持一致，下一次查询重新加载。
    for row in list(session.identity_map.values()):
        if isinstance(row, ContentDailyStat):
            session.expire(row)


async def record_collection_stat(session: AsyncSession, run: CollectRun) -> None:
    """采集完成后累计一行日级统计；历史明细为空时不伪造。"""
    finished = run.finished_at or run.started_at
    day = beijing_date(finished)
    status = str(run.status)
    row = await session.get(
        CollectionDailyStat,
        {"stat_date": day, "target_id": run.target_id, "status": status},
    )
    if row is None:
        row = CollectionDailyStat(
            stat_date=day,
            target_id=run.target_id,
            status=status,
            runs=0,
            detailed_runs=0,
            items_fetched=0,
            items_filtered=0,
            items_found=0,
            items_new=0,
            items_updated=0,
            parse_failed=0,
            official_items=0,
            community_items=0,
            duration_ms=0,
            updated_at=utcnow(),
        )
        session.add(row)
    row.runs += 1
    row.items_found += run.items_found
    row.items_new += run.items_new
    if run.items_fetched is not None:
        row.detailed_runs += 1
        row.items_fetched += run.items_fetched
        row.items_filtered += run.items_filtered or 0
        row.items_updated += run.items_updated or 0
        row.parse_failed += run.parse_failed or 0
        row.official_items += run.official_items or 0
        row.community_items += run.community_items or 0
        row.duration_ms += run.duration_ms or 0
    row.updated_at = utcnow()


def _rate(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


async def build_statistics(session: AsyncSession, days: int) -> StatisticsOverview:
    today = beijing_date(utcnow())
    start = today - timedelta(days=days - 1)

    lifetime = await session.execute(
        select(
            func.coalesce(func.sum(ContentDailyStat.contents_new), 0),
            func.coalesce(
                func.sum(
                    case(
                        (ContentDailyStat.identity == "official", ContentDailyStat.contents_new),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(ContentDailyStat.keyword == "")
    )
    total_contents, official_contents = (int(value or 0) for value in lifetime.one())

    content_rows = list(
        await session.scalars(
            select(ContentDailyStat).where(
                ContentDailyStat.keyword == "",
                ContentDailyStat.stat_date >= start,
                ContentDailyStat.stat_date <= today,
            )
        )
    )
    trend_map: dict[date, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "official": 0, "community": 0}
    )
    platform_map: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "official": 0, "community": 0}
    )
    identity_map: dict[str, int] = defaultdict(int)
    for row in content_rows:
        community = row.identity != "official"
        trend_map[row.stat_date]["total"] += row.contents_new
        trend_map[row.stat_date]["community" if community else "official"] += row.contents_new
        platform_map[row.platform]["total"] += row.contents_new
        platform_map[row.platform]["community" if community else "official"] += row.contents_new
        identity_map[row.identity] += row.contents_new

    trend = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        values = trend_map[day]
        trend.append(StatisticsTrendPoint(date=day, **values))
    period_new = sum(point.total for point in trend)

    platforms = [
        StatisticsPlatform(
            platform=platform,
            total=values["total"],
            official=values["official"],
            community=values["community"],
            share=_rate(values["total"], period_new),
        )
        for platform, values in sorted(
            platform_map.items(), key=lambda item: item[1]["total"], reverse=True
        )
    ]
    identities = [
        StatisticsIdentity(identity=identity, total=count, share=_rate(count, period_new))
        for identity, count in sorted(identity_map.items(), key=lambda item: item[1], reverse=True)
    ]

    keyword_rows = await session.execute(
        select(ContentDailyStat.keyword, func.sum(ContentDailyStat.contents_new))
        .where(
            ContentDailyStat.keyword != "",
            ContentDailyStat.stat_date >= start,
            ContentDailyStat.stat_date <= today,
        )
        .group_by(ContentDailyStat.keyword)
        .order_by(func.sum(ContentDailyStat.contents_new).desc())
        .limit(12)
    )
    keywords = [
        StatisticsKeyword(keyword=keyword, contents=int(count or 0))
        for keyword, count in keyword_rows
    ]

    collection_rows = list(
        await session.execute(
            select(
                CollectionDailyStat,
                CollectTarget.display_name,
                CollectTarget.target_value,
                CollectTarget.platform,
                CollectTarget.target_type,
            )
            .join(CollectTarget, CollectTarget.id == CollectionDailyStat.target_id)
            .where(
                CollectionDailyStat.stat_date >= start,
                CollectionDailyStat.stat_date <= today,
            )
        )
    )
    collection_map: dict[date, dict[str, int]] = defaultdict(
        lambda: {
            "runs": 0,
            "failed": 0,
            "items_found": 0,
            "items_new": 0,
            "detailed_runs": 0,
            "items_fetched": 0,
            "items_filtered": 0,
            "items_updated": 0,
        }
    )
    target_map: dict[int, dict] = {}
    successful_runs = 0
    for row, display_name, target_value, platform, target_type in collection_rows:
        # 冷却跳过没有访问平台；进行中也还没有最终结果，均不进入成功率分母。
        if row.status not in (RunStatus.SUCCESS, RunStatus.PARTIAL, RunStatus.FAILED):
            continue
        successful_runs += row.runs if row.status == RunStatus.SUCCESS else 0
        daily = collection_map[row.stat_date]
        daily["runs"] += row.runs
        daily["failed"] += row.runs if row.status == RunStatus.FAILED else 0
        daily["items_found"] += row.items_found
        daily["items_new"] += row.items_new
        daily["detailed_runs"] += row.detailed_runs
        daily["items_fetched"] += row.items_fetched
        daily["items_filtered"] += row.items_filtered
        daily["items_updated"] += row.items_updated

        target = target_map.setdefault(
            row.target_id,
            {
                "target_id": row.target_id,
                "label": display_name or target_value,
                "platform": platform,
                "target_type": target_type,
                "runs": 0,
                "items_found": 0,
                "items_new": 0,
            },
        )
        target["runs"] += row.runs
        target["items_found"] += row.items_found
        target["items_new"] += row.items_new

    collection = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        collection.append(StatisticsCollectionPoint(date=day, **collection_map[day]))
    targets = [
        StatisticsTarget(
            **values,
            yield_rate=_rate(values["items_new"], values["items_found"]),
        )
        for values in sorted(
            target_map.values(), key=lambda value: value["items_found"], reverse=True
        )
    ]

    run_count = sum(point.runs for point in collection)
    failed_runs = sum(point.failed for point in collection)
    period_found = sum(point.items_found for point in collection)
    period_run_new = sum(point.items_new for point in collection)
    latest_collection_at = await session.scalar(
        select(func.max(CollectRun.finished_at)).where(
            CollectRun.status.in_([RunStatus.SUCCESS, RunStatus.PARTIAL])
        )
    )
    details_since = await session.scalar(
        select(func.min(CollectRun.finished_at)).where(CollectRun.items_fetched.is_not(None))
    )

    recent_rows = await session.execute(
        select(CollectRun, CollectTarget.display_name, CollectTarget.target_value, CollectTarget.platform)
        .join(CollectTarget, CollectTarget.id == CollectRun.target_id)
        .order_by(CollectRun.started_at.desc())
        .limit(20)
    )
    recent_runs = [
        StatisticsRecentRun(
            id=run.id,
            target=display_name or target_value,
            platform=platform,
            status=str(run.status),
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_ms=run.duration_ms,
            items_fetched=run.items_fetched,
            items_filtered=run.items_filtered,
            items_found=run.items_found,
            items_new=run.items_new,
            items_updated=run.items_updated,
        )
        for run, display_name, target_value, platform in recent_rows
    ]

    return StatisticsOverview(
        range_days=days,
        generated_at=utcnow(),
        details_since=details_since,
        totals=StatisticsTotals(
            total_contents=total_contents,
            official_contents=official_contents,
            community_contents=max(0, total_contents - official_contents),
            period_new=period_new,
            collection_runs=run_count,
            failed_runs=failed_runs,
            success_rate=_rate(successful_runs, run_count),
            items_found=period_found,
            items_new=period_run_new,
            yield_rate=_rate(period_run_new, period_found),
            latest_collection_at=latest_collection_at,
        ),
        trend=trend,
        platforms=platforms,
        identities=identities,
        keywords=keywords,
        collection=collection,
        targets=targets,
        recent_runs=recent_runs,
    )
