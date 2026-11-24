"""数据库结构优化测试：latest_version_id 自动维护、view_count 自动提取、迁移 0059 幂等与查询一致性。"""

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

from app.clock import utcnow
from app.models import ContentVersion, MetricSnapshot, RawContent
from app.models.content import extract_view_count
from app.services.curation import select_trending

_MIG_PATH = Path(__file__).parents[1] / "alembic/versions/0059_query_performance_indexes.py"
_SPEC = importlib.util.spec_from_file_location("mig_0059", _MIG_PATH)
assert _SPEC and _SPEC.loader
migration_0059 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration_0059)


def test_extract_view_count_handles_edge_cases():
    assert extract_view_count({"view": 12345}) == 12345
    assert extract_view_count({"view": 0}) == 0
    assert extract_view_count({"view": 98.7}) == 98
    assert extract_view_count({"view": "4200"}) == 4200
    assert extract_view_count({"view": " 314.9 "}) == 314
    assert extract_view_count({"view": True}) is None
    assert extract_view_count({"view": False}) is None
    assert extract_view_count({"view": ""}) is None
    assert extract_view_count({"view": "abc"}) is None
    assert extract_view_count({"like": 50}) is None
    assert extract_view_count(None) is None


@pytest.mark.asyncio
async def test_latest_version_id_synced_across_lifecycle(session):
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id="BV_PERF_1",
        community="wuwa",
        content_hash="h1",
        raw_json={},
    )
    session.add(raw)
    await session.flush()
    assert raw.latest_version_id is None

    v1 = ContentVersion(raw_content_id=raw.id, version=1, title="标题 v1", text="正文 v1")
    session.add(v1)
    await session.flush()
    assert raw.latest_version_id == v1.id

    v2 = ContentVersion(raw_content_id=raw.id, version=2, title="标题 v2", text="正文 v2")
    session.add(v2)
    await session.flush()
    assert raw.latest_version_id == v2.id

    # 插入旧版本号不能回退 latest_version_id
    v0 = ContentVersion(raw_content_id=raw.id, version=0, title="标题 v0", text="正文 v0")
    session.add(v0)
    await session.flush()
    assert raw.latest_version_id == v2.id

    # 删除最新版本后应回退到剩余最高版本
    await session.delete(v2)
    await session.flush()
    assert raw.latest_version_id == v1.id

    # 删除全部版本后回退为 None
    await session.delete(v1)
    await session.delete(v0)
    await session.flush()
    assert raw.latest_version_id is None

    await session.commit()
    refreshed = await session.scalar(select(RawContent.latest_version_id).where(RawContent.id == raw.id))
    assert refreshed is None


@pytest.mark.asyncio
async def test_raw_and_version_created_in_same_flush(session):
    raw = RawContent(
        platform="bilibili",
        content_type="video",
        external_id="BV_PERF_SAME_FLUSH",
        community="wuwa",
        content_hash="h_same",
        raw_json={},
    )
    v1 = ContentVersion(raw_content=raw, version=1, title="同批写入", text="正文")
    session.add_all([raw, v1])
    await session.commit()

    assert raw.latest_version_id == v1.id
    db_val = await session.scalar(select(RawContent.latest_version_id).where(RawContent.id == raw.id))
    assert db_val == v1.id


@pytest.mark.asyncio
async def test_metric_snapshot_view_count_synced_and_hot_trending_queries(session, client):
    now = utcnow()
    r1 = RawContent(
        platform="bilibili",
        content_type="video",
        external_id="BV_HOT_1",
        community="wuwa",
        published_at=now - timedelta(hours=2),
        content_hash="hh1",
        raw_json={},
    )
    r2 = RawContent(
        platform="bilibili",
        content_type="video",
        external_id="BV_HOT_2",
        community="wuwa",
        published_at=now - timedelta(hours=1),
        content_hash="hh2",
        raw_json={},
    )
    session.add_all([r1, r2])
    await session.flush()
    session.add_all([
        ContentVersion(raw_content_id=r1.id, version=1, title="热门一号", text="正文"),
        ContentVersion(raw_content_id=r2.id, version=1, title="热门二号", text="正文"),
    ])
    s1_a = MetricSnapshot(raw_content_id=r1.id, metrics={"view": "1000"}, captured_at=now - timedelta(hours=2))
    s1_b = MetricSnapshot(raw_content_id=r1.id, metrics={"view": 9500}, captured_at=now - timedelta(minutes=10))
    s2_a = MetricSnapshot(raw_content_id=r2.id, metrics={"view": 5000}, captured_at=now - timedelta(hours=1))
    s2_b = MetricSnapshot(raw_content_id=r2.id, metrics={"view": 6000}, captured_at=now - timedelta(minutes=5))
    session.add_all([s1_a, s1_b, s2_a, s2_b])
    await session.commit()

    assert s1_a.view_count == 1000
    assert s1_b.view_count == 9500
    assert s2_a.view_count == 5000
    assert s2_b.view_count == 6000

    # 更新 metrics JSON 时 view_count 自动同步
    s2_b.metrics = {"view": 7200}
    await session.commit()
    assert s2_b.view_count == 7200

    # /api/contents?sort=hot 按最新 view_count 降序
    res = await client.get("/api/contents", params={"community": "wuwa", "sort": "hot"})
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()["items"]]
    assert ids == [r1.id, r2.id]

    # select_trending 按窗口内 view_count 增量降序：r1 (+8500) > r2 (+2200)
    trending = await select_trending(session, limit=5, community="wuwa", hours=24)
    assert [item["id"] for item in trending] == [r1.id, r2.id]
    assert trending[0]["delta_view"] == 8500
    assert trending[1]["delta_view"] == 2200


def test_migration_0059_upgrade_backfill_idempotent_and_downgrade():
    sync_engine = sa.create_engine("sqlite://")
    with sync_engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE raw_contents (
                id INTEGER PRIMARY KEY,
                community VARCHAR(32) NOT NULL DEFAULT 'wuwa',
                hidden BOOLEAN NOT NULL DEFAULT 0,
                published_at DATETIME
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE content_versions (
                id INTEGER PRIMARY KEY,
                raw_content_id INTEGER NOT NULL,
                version INTEGER NOT NULL
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE metric_snapshots (
                id INTEGER PRIMARY KEY,
                raw_content_id INTEGER NOT NULL,
                metrics JSON NOT NULL
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE content_matches (
                id INTEGER PRIMARY KEY,
                is_same_event BOOLEAN NOT NULL,
                confidence FLOAT NOT NULL,
                left_raw_id INTEGER NOT NULL,
                right_raw_id INTEGER NOT NULL
            )
        """))
        conn.execute(sa.text("INSERT INTO raw_contents (id, community, hidden) VALUES (1, 'wuwa', 0), (2, 'wuwa', 0)"))
        conn.execute(sa.text("INSERT INTO content_versions (id, raw_content_id, version) VALUES (10, 1, 1), (11, 1, 2)"))
        conn.execute(sa.text(
            "INSERT INTO metric_snapshots (id, raw_content_id, metrics) VALUES "
            "(100, 1, '{\"view\": 4321}'), (101, 1, '{\"like\": 12}')"
        ))

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_0059.upgrade()
            # 再次执行 upgrade 应完全幂等
            migration_0059.upgrade()

        latest_map = dict(conn.execute(sa.text("SELECT id, latest_version_id FROM raw_contents")).all())
        assert latest_map == {1: 11, 2: None}

        view_map = dict(conn.execute(sa.text("SELECT id, view_count FROM metric_snapshots")).all())
        assert view_map == {100: 4321, 101: None}

        inspector = sa.inspect(conn)
        raw_indexes = {idx["name"] for idx in inspector.get_indexes("raw_contents")}
        assert "ix_raw_contents_latest_version_id" in raw_indexes
        assert "ix_raw_contents_comm_hidden_pub" in raw_indexes
        assert "ix_raw_contents_hidden_pub" in raw_indexes
        ms_indexes = {idx["name"] for idx in inspector.get_indexes("metric_snapshots")}
        assert "ix_metric_snapshots_content_id_view" in ms_indexes
        cm_indexes = {idx["name"] for idx in inspector.get_indexes("content_matches")}
        assert "ix_content_matches_same_conf_pair" in cm_indexes

        with Operations.context(ctx):
            migration_0059.downgrade()

        inspector = sa.inspect(conn)
        assert not any(col["name"] == "latest_version_id" for col in inspector.get_columns("raw_contents"))
        assert not any(col["name"] == "view_count" for col in inspector.get_columns("metric_snapshots"))
    sync_engine.dispose()
