"""ingest 幂等性单测：同内容重复采集不产生重复记录，变化才产生新版本。"""

from sqlalchemy import func, select

from app.models import ContentVersion, MetricSnapshot, RawContent, Source
from app.schemas.content import FetchResult  # noqa: F401
from app.services.ingest import ingest_items

from tests.test_parsers_bilibili import DYNAMIC_RAW, VIDEO_RAW


async def test_ingest_new_item(session):
    stats = await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    assert stats.items_found == 1
    assert stats.items_new == 1
    assert stats.parse_failed == 0

    raw = await session.scalar(select(RawContent))
    assert raw.external_id == "BV1Mcgp6ME1x"
    assert raw.raw_json["title"] == "视频标题"
    versions = (await session.scalars(select(ContentVersion))).all()
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].title == "视频标题"
    snapshots = (await session.scalars(select(MetricSnapshot))).all()
    assert len(snapshots) == 1
    assert snapshots[0].metrics["view"] == 680


async def test_ingest_repeated_is_idempotent(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    stats = await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    assert stats.items_new == 0
    assert stats.items_updated == 0

    assert await session.scalar(select(func.count(RawContent.id))) == 1
    # 指标未变也记快照（时间序列），但版本不增加
    assert await session.scalar(select(func.count(ContentVersion.id))) == 1
    assert await session.scalar(select(func.count(MetricSnapshot.id))) == 2


async def test_ingest_changed_content_creates_new_version(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    changed = dict(VIDEO_RAW, title="改过的标题", view_count=99999)
    stats = await ingest_items(session, "bilibili_search", [changed])
    assert stats.items_updated == 1

    raw = await session.scalar(select(RawContent))
    # 原始数据不可覆盖：raw_json 保留首次采集结果
    assert raw.raw_json["title"] == "视频标题"
    versions = (
        await session.scalars(select(ContentVersion).order_by(ContentVersion.version))
    ).all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].title == "改过的标题"


async def test_ingest_metrics_only_change_no_new_version(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    hotter = dict(VIDEO_RAW, view_count=100000, like_count=5000)
    stats = await ingest_items(session, "bilibili_search", [hotter])
    assert stats.items_updated == 0
    assert await session.scalar(select(func.count(ContentVersion.id))) == 1
    snapshots = (
        await session.scalars(select(MetricSnapshot).order_by(MetricSnapshot.id))
    ).all()
    assert snapshots[-1].metrics["view"] == 100000


async def test_ingest_upserts_source(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    updated = dict(VIDEO_RAW, owner_name="新名字")
    await ingest_items(session, "bilibili_search", [updated])
    sources = (await session.scalars(select(Source))).all()
    assert len(sources) == 1
    assert sources[0].name == "新名字"


async def test_ingest_dynamic_official_source(session):
    await ingest_items(session, "bilibili_dynamics", [DYNAMIC_RAW])
    source = await session.scalar(select(Source))
    assert source.external_id == "1955897084"
    assert source.identity == "official"


async def test_ingest_parse_failure_counted_not_fatal(session):
    stats = await ingest_items(session, "bilibili_search", [{"junk": True}, VIDEO_RAW])
    assert stats.parse_failed == 1
    assert stats.items_new == 1


async def test_ingest_unknown_fetch_kind_rejected(session):
    try:
        await ingest_items(session, "nope", [])
    except ValueError as exc:
        assert "no parser" in str(exc)
    else:
        raise AssertionError("expected ValueError")
