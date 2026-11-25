"""数据保留：折叠内容的重复版本、抽稀旧指标快照。

每天跑一次。全部分批提交，单个事务只占短暂的锁，不挡采集写入。
"""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.models import (
    AiUsage,
    BotDelivery,
    CollectRun,
    ContentVersion,
    CustomCollectRun,
    MerchScan,
    MetricSnapshot,
    NewsGrouping,
    NotificationDelivery,
    RunStatus,
)
from app.services.fingerprint import content_body

# 少于这个版本数的内容不可能有可折叠的版本：首版、最新版和最新正文的上一次出现都要留。
MIN_VERSIONS_TO_COMPACT = 4
# 热度窗口最长 7 天，详情页只展示最近 10 个点。30 天内保留全部快照，更早的每天留一条。
METRIC_FULL_DAYS = 30
# 运维流水表保留期：采集流水 30 天（日统计表长期保留），AI 用量与投递流水 90 天（覆盖最大 90 天统计窗）。
COLLECT_RUN_RETENTION_DAYS = 30
AI_USAGE_RETENTION_DAYS = 90
DELIVERY_RETENTION_DAYS = 90
BATCH_SIZE = 1000
MAX_PRUNE_BATCHES = 50
_ITEMS_PER_TRANSACTION = 50


def removable_versions(rows: list[tuple[int, tuple]], referenced: set[int]) -> list[int]:
    """rows 为按版本升序的 (版本号, 正文键)，返回可以删除的版本号。

    保留首版、最新版、每份正文的最后一次出现和被其他表引用的版本。最新正文再多留上一次
    出现：ingest 靠近期版本里的重复认出抖动，全部折叠后这条内容会被当成没抖过。
    """
    if not rows:
        return []
    latest_body = rows[-1][1]
    last_seen = {body: version for version, body in rows}
    repeats = [version for version, body in rows if body == latest_body]
    keep = {rows[0][0], *last_seen.values(), *repeats[-2:], *referenced}
    return [version for version, _ in rows if version not in keep]


async def _referenced_versions(session: AsyncSession, raw_ids: list[int]) -> dict[int, set[int]]:
    """商品分析和资讯分组按版本号记录判定依据，这些版本不能折叠。"""
    referenced: dict[int, set[int]] = defaultdict(set)
    scans = await session.execute(
        select(MerchScan.raw_content_id, MerchScan.content_version, MerchScan.result)
        .where(MerchScan.raw_content_id.in_(raw_ids))
    )
    for raw_id, version, result in scans:
        referenced[raw_id].add(version)
        fulfillment_version = ((result or {}).get("fulfillment") or {}).get("content_version")
        if isinstance(fulfillment_version, int):
            referenced[raw_id].add(fulfillment_version)
    groupings = await session.execute(
        select(NewsGrouping.raw_content_id, NewsGrouping.content_version)
        .where(NewsGrouping.raw_content_id.in_(raw_ids))
    )
    for raw_id, version in groupings:
        referenced[raw_id].add(version)
    return referenced


async def compact_versions(session: AsyncSession) -> int:
    """删除内容的重复版本，返回删除行数。版本号不重排，留下的版本号保持原样。"""
    raw_ids = list(await session.scalars(
        select(ContentVersion.raw_content_id)
        .group_by(ContentVersion.raw_content_id)
        .having(func.count() >= MIN_VERSIONS_TO_COMPACT)
    ))
    removed = 0
    for start in range(0, len(raw_ids), _ITEMS_PER_TRANSACTION):
        chunk = raw_ids[start:start + _ITEMS_PER_TRANSACTION]
        referenced = await _referenced_versions(session, chunk)
        rows = await session.execute(
            select(ContentVersion.raw_content_id, ContentVersion.id, ContentVersion.version,
                   ContentVersion.title, ContentVersion.text, ContentVersion.normalized_json)
            .where(ContentVersion.raw_content_id.in_(chunk))
            .order_by(ContentVersion.raw_content_id, ContentVersion.version)
        )
        by_item: dict[int, list[tuple[int, tuple]]] = defaultdict(list)
        row_ids: dict[tuple[int, int], int] = {}
        for raw_id, row_id, version, title, text, data in rows:
            data = data or {}
            by_item[raw_id].append((version, content_body(title, text, data.get("media"), data.get("extra"))))
            row_ids[raw_id, version] = row_id
        doomed = [
            row_ids[raw_id, version]
            for raw_id, versions in by_item.items()
            for version in removable_versions(versions, referenced.get(raw_id, set()))
        ]
        for offset in range(0, len(doomed), BATCH_SIZE):
            await session.execute(
                delete(ContentVersion).where(ContentVersion.id.in_(doomed[offset:offset + BATCH_SIZE]))
            )
        await session.commit()
        removed += len(doomed)
    return removed


async def thin_metric_snapshots(session: AsyncSession, *, now: datetime | None = None) -> int:
    """超过保留期的快照每条内容每天只留最后一条，返回删除行数。"""
    cutoff = (now or utcnow()) - timedelta(days=METRIC_FULL_DAYS)
    keep = set(await session.scalars(
        select(func.max(MetricSnapshot.id))
        .where(MetricSnapshot.captured_at < cutoff)
        .group_by(MetricSnapshot.raw_content_id, func.date(MetricSnapshot.captured_at))
    ))
    removed = 0
    cursor = 0
    while True:
        ids = list(await session.scalars(
            select(MetricSnapshot.id)
            .where(MetricSnapshot.id > cursor, MetricSnapshot.captured_at < cutoff)
            .order_by(MetricSnapshot.id)
            .limit(BATCH_SIZE)
        ))
        if not ids:
            return removed
        cursor = ids[-1]
        stale = [snapshot_id for snapshot_id in ids if snapshot_id not in keep]
        if stale:
            await session.execute(delete(MetricSnapshot).where(MetricSnapshot.id.in_(stale)))
        await session.commit()
        removed += len(stale)


async def _prune_by_cutoff(
    session: AsyncSession,
    model,
    time_column,
    cutoff: datetime,
    *,
    extra_conditions: tuple = (),
    max_batches: int = MAX_PRUNE_BATCHES,
) -> int:
    """按主键分批小事务删除超期流水行，不长持表锁。"""
    removed = 0
    for _ in range(max_batches):
        ids = list(
            await session.scalars(
                select(model.id)
                .where(time_column < cutoff, *extra_conditions)
                .order_by(model.id)
                .limit(BATCH_SIZE)
            )
        )
        if not ids:
            break
        await session.execute(delete(model).where(model.id.in_(ids)))
        await session.commit()
        removed += len(ids)
    return removed


async def prune_operational_logs(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, int]:
    """清理超期的采集运行记录、AI 用量明细与历史投递流水。"""
    now = now or utcnow()
    run_cutoff = now - timedelta(days=COLLECT_RUN_RETENTION_DAYS)
    ai_cutoff = now - timedelta(days=AI_USAGE_RETENTION_DAYS)
    delivery_cutoff = now - timedelta(days=DELIVERY_RETENTION_DAYS)

    collect_runs_removed = await _prune_by_cutoff(
        session,
        CollectRun,
        CollectRun.started_at,
        run_cutoff,
        extra_conditions=(CollectRun.status != RunStatus.RUNNING,),
    )
    custom_runs_removed = await _prune_by_cutoff(
        session,
        CustomCollectRun,
        CustomCollectRun.started_at,
        run_cutoff,
    )
    ai_usage_removed = await _prune_by_cutoff(
        session,
        AiUsage,
        AiUsage.created_at,
        ai_cutoff,
    )
    bot_deliveries_removed = await _prune_by_cutoff(
        session,
        BotDelivery,
        BotDelivery.created_at,
        delivery_cutoff,
        extra_conditions=(BotDelivery.status.notin_(("pending", "sending")),),
    )
    notification_deliveries_removed = await _prune_by_cutoff(
        session,
        NotificationDelivery,
        NotificationDelivery.created_at,
        delivery_cutoff,
        extra_conditions=(NotificationDelivery.status.notin_(("pending", "sending")),),
    )
    return {
        "collect_runs_removed": collect_runs_removed,
        "custom_runs_removed": custom_runs_removed,
        "ai_usage_removed": ai_usage_removed,
        "deliveries_removed": bot_deliveries_removed + notification_deliveries_removed,
    }


async def run_retention(session: AsyncSession, *, now: datetime | None = None) -> dict:
    from app.services.steam import thin_steam_snapshots

    log_stats = await prune_operational_logs(session, now=now)
    return {
        "versions_removed": await compact_versions(session),
        "snapshots_removed": await thin_metric_snapshots(session, now=now),
        "steam_snapshots_removed": await thin_steam_snapshots(session, now=now),
        **log_stats,
    }
