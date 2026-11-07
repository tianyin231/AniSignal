"""数据保留：折叠重复版本要保住被引用的版本和防抖能力，抽稀快照不能丢最新值。"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.clock import utcnow
from app.models import ContentVersion, MerchScan, MetricSnapshot, NewsGrouping, RawContent
from app.scheduler import loop
from app.services.ingest import ingest_items
from app.services.retention import (
    METRIC_FULL_DAYS,
    compact_versions,
    removable_versions,
    run_retention,
    thin_metric_snapshots,
)
from tests.test_parsers_bilibili import DYNAMIC_RAW


async def add_item(session, name, bodies):
    raw = RawContent(platform="bilibili", content_type="dynamic", external_id=name, community="wuwa")
    session.add(raw)
    await session.flush()
    for version, text in enumerate(bodies, start=1):
        session.add(ContentVersion(raw_content_id=raw.id, version=version, title="t", text=text))
    await session.commit()
    return raw


async def versions_of(session, raw):
    rows = await session.execute(
        select(ContentVersion.version, ContentVersion.text)
        .where(ContentVersion.raw_content_id == raw.id).order_by(ContentVersion.version))
    return rows.all()


def test_keeps_first_latest_last_of_each_body_and_previous_latest():
    rows = [(1, "A"), (2, "B"), (3, "A"), (4, "B"), (5, "A"), (6, "B"), (7, "A")]
    # 首版 1、B 的最后一次 6、最新 A 的最后两次 5 和 7
    assert removable_versions(rows, set()) == [2, 3, 4]


def test_referenced_versions_survive():
    rows = [(1, "A"), (2, "B"), (3, "A"), (4, "B"), (5, "A"), (6, "B"), (7, "A")]
    assert removable_versions(rows, {3}) == [2, 4]


def test_history_without_repeats_is_untouched():
    assert removable_versions([(1, "A"), (2, "B"), (3, "C"), (4, "D")], set()) == []
    assert removable_versions([], set()) == []


async def test_compact_removes_duplicates_and_keeps_version_numbers(session):
    raw = await add_item(session, "flap", ["A", "B"] * 4)
    clean = await add_item(session, "clean", ["A", "B", "C", "D"])

    assert await compact_versions(session) == 4

    assert [version for version, _ in await versions_of(session, raw)] == [1, 6, 7, 8]
    assert len(await versions_of(session, clean)) == 4


async def test_compact_keeps_versions_used_by_scans_and_groupings(session):
    raw = await add_item(session, "ref", ["A", "B"] * 4)
    session.add(MerchScan(raw_content_id=raw.id, content_version=3, result={"fulfillment": {"content_version": 2}}))
    session.add(NewsGrouping(
        raw_content_id=raw.id, content_version=4, content_hash="h", review_key="k", relation="new", prompt_version="p"))
    await session.commit()

    await compact_versions(session)

    assert [version for version, _ in await versions_of(session, raw)] == [1, 2, 3, 4, 6, 7, 8]


async def test_flap_guard_still_works_after_compaction(session):
    """折叠后再遇到轮播的旧正文不能重新长版本。"""
    base = dict(DYNAMIC_RAW, pics=[])
    for text in ("正文 A", "正文 B", "正文 A", "正文 B", "正文 A", "正文 B", "正文 A"):
        await ingest_items(session, "bilibili_dynamics", [dict(base, text=text)])
    raw = await session.scalar(select(RawContent))
    for version, text in enumerate(("正文 B", "正文 A", "正文 B", "正文 A"), start=4):
        session.add(ContentVersion(raw_content_id=raw.id, version=version, title="t", text=text))
    await session.commit()
    await compact_versions(session)
    count = len(await versions_of(session, raw))

    for text in ("正文 B", "正文 A", "正文 B"):
        await ingest_items(session, "bilibili_dynamics", [dict(base, text=text)])

    assert len(await versions_of(session, raw)) == count


async def test_thinning_keeps_last_of_each_old_day_and_every_recent_snapshot(session):
    now = datetime(2026, 11, 7, 12)
    raw = await add_item(session, "metrics", ["A"])
    old_day = now - timedelta(days=METRIC_FULL_DAYS + 5)
    recent = now - timedelta(days=2)
    stamps = [old_day.replace(hour=hour) for hour in (1, 5, 9)] + [recent.replace(hour=hour) for hour in (1, 5)]
    session.add_all([
        MetricSnapshot(raw_content_id=raw.id, metrics={"view": index}, captured_at=stamp)
        for index, stamp in enumerate(stamps)
    ])
    await session.commit()

    assert await thin_metric_snapshots(session, now=now) == 2

    views = list(await session.scalars(select(MetricSnapshot.id).where(MetricSnapshot.raw_content_id == raw.id)))
    assert len(views) == 3
    kept = list(await session.execute(select(MetricSnapshot.captured_at).order_by(MetricSnapshot.captured_at)))
    assert [row[0].hour for row in kept] == [9, 1, 5]


async def test_thinning_keeps_the_only_snapshot_of_a_stale_item(session):
    now = utcnow()
    raw = await add_item(session, "stale", ["A"])
    session.add(MetricSnapshot(raw_content_id=raw.id, metrics={"view": 1}, captured_at=now - timedelta(days=200)))
    await session.commit()

    assert (await run_retention(session, now=now)) == {"versions_removed": 0, "snapshots_removed": 0}


class FakeRedis:
    def __init__(self):
        self.keys, self.jobs = {}, []

    async def get(self, key):
        return self.keys.get(key)

    async def set(self, key, value, **_):
        self.keys[key] = value

    async def enqueue_job(self, name, **kwargs):
        self.jobs.append((name, kwargs))


async def test_retention_runs_once_inside_the_night_window():
    redis = FakeRedis()
    # UTC 19:30 是北京时间 03:30
    await loop._enqueue_retention_if_due(redis, datetime(2026, 11, 7, 19, 30))
    await loop._enqueue_retention_if_due(redis, datetime(2026, 11, 7, 20, 0))
    assert [name for name, _ in redis.jobs] == ["retention_sweep"]


async def test_retention_does_not_fire_outside_the_night_window():
    redis = FakeRedis()
    await loop._enqueue_retention_if_due(redis, datetime(2026, 11, 7, 10, 0))
    assert redis.jobs == []
