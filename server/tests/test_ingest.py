"""ingest 幂等性单测：同内容重复采集不产生重复记录，变化才产生新版本。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import ContentDailyStat, ContentVersion, MetricSnapshot, RawContent, Source
from app.schemas.content import FetchResult  # noqa: F401
from app.services.ingest import SNAPSHOT_HEARTBEAT, ingest_items

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


async def test_concurrent_insert_of_the_same_item_is_retried_as_an_update(session, monkeypatch):
    """另一目标刚提交了同一条内容：撞唯一键后换新快照重试，这一条按已存在更新，不等下一轮。"""
    from sqlalchemy.exc import IntegrityError

    from app.services import ingest as ingest_module

    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    real, calls = ingest_module._ingest_one, []

    async def collide_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise IntegrityError("INSERT INTO raw_contents", {}, Exception(1062, "Duplicate entry"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "_ingest_one", collide_once)
    stats = await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, title="改过的标题"), dict(VIDEO_RAW, bvid="other")])
    assert len(calls) == 3
    assert stats.errors == []
    assert stats.items_updated == 1 and stats.items_new == 1
    assert await session.scalar(select(func.count()).select_from(RawContent)) == 2


async def test_repeated_collision_fails_only_that_item(session, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.services import ingest as ingest_module

    real = ingest_module._ingest_one

    async def collide(session_, normalized, *args, **kwargs):
        if normalized.external_id == "stuck":
            raise IntegrityError("INSERT INTO raw_contents", {}, Exception(1062, "Duplicate entry"))
        return await real(session_, normalized, *args, **kwargs)

    monkeypatch.setattr(ingest_module, "_ingest_one", collide)
    stats = await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, bvid="stuck"), VIDEO_RAW])
    assert len(stats.errors) == 1 and "IntegrityError" in stats.errors[0]
    assert stats.items_new == 1


async def test_reparse_can_target_only_selected_contents_and_empty_list_is_noop(session):
    from app.services.classify import seed_defaults
    from app.services.ingest import reparse_contents

    await ingest_items(session, "bilibili_search", [
        dict(VIDEO_RAW, bvid="selected", title="爱弥斯键盘品控拉完了"),
        dict(VIDEO_RAW, bvid="untouched", title="另一键盘品控拉完了"),
    ])
    raws = {row.external_id: row for row in (await session.scalars(select(RawContent))).all()}
    await seed_defaults(session)
    result = await reparse_contents(session, "bilibili", "video", content_ids=[raws["selected"].id])
    assert result == {"checked": 1, "updated": 1, "failed": 0}
    versions = (await session.scalars(select(ContentVersion).order_by(ContentVersion.version.desc()))).all()
    assert [(row.version, row.category) for row in versions if row.raw_content_id == raws["selected"].id] == [(2, "quality"), (1, None)]
    assert [(row.version, row.category) for row in versions if row.raw_content_id == raws["untouched"].id] == [(1, None)]
    assert await reparse_contents(session, "bilibili", content_ids=[]) == {"checked": 0, "updated": 0, "failed": 0}
    assert await reparse_contents(session, "bilibili", "video", content_ids=[raws["selected"].id]) == {"checked": 1, "updated": 0, "failed": 0}


async def test_ingest_repeated_is_idempotent(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    stats = await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    assert stats.items_new == 0
    assert stats.items_updated == 0

    assert await session.scalar(select(func.count(RawContent.id))) == 1
    # 内容和指标都没变：版本不增加，快照也不重复记录
    assert await session.scalar(select(func.count(ContentVersion.id))) == 1
    assert await session.scalar(select(func.count(MetricSnapshot.id))) == 1


async def test_ingest_updates_daily_statistics_without_double_counting(session):
    item = dict(VIDEO_RAW, _matched_keywords=["鸣潮", "新角色"])
    second = dict(item, bvid="BV1StatsSecond", aid=999_002)
    await ingest_items(session, "bilibili_search", [item, second])
    await ingest_items(session, "bilibili_search", [item])

    rows = list(await session.scalars(select(ContentDailyStat)))
    assert {row.keyword: row.contents_new for row in rows} == {
        "": 2,
        "鸣潮": 2,
        "新角色": 2,
    }

    await ingest_items(
        session,
        "bilibili_search",
        [dict(VIDEO_RAW, _matched_keywords=["鸣潮", "版本预告"])],
    )
    rows = list(await session.scalars(select(ContentDailyStat)))
    assert {row.keyword: row.contents_new for row in rows}["版本预告"] == 1


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


@pytest.mark.parametrize(
    "complete_fields",
    [
        {"type": "DYNAMIC_TYPE_DRAW", "text": "活动详情", "pics": ["https://example.com/a.jpg"]},
        {"type": "DYNAMIC_TYPE_ARTICLE", "text": "专栏正文", "pics": []},
        {"type": "DYNAMIC_TYPE_DRAW", "text": "", "pics": ["https://example.com/a.jpg"]},
    ],
)
@pytest.mark.parametrize("empty_text", ["", " \n\t "])
async def test_ingest_empty_dynamic_keeps_complete_version_and_updates_observations(
    session, complete_fields, empty_text
):
    complete = dict(DYNAMIC_RAW, **complete_fields, _matched_keywords=["鸣潮"])
    await ingest_items(session, "bilibili_dynamics", [complete])
    content = await session.scalar(select(RawContent))
    original_hash = content.content_hash
    previous_seen = datetime(2020, 1, 1)
    content.last_seen_at = previous_seen
    await session.commit()

    empty = dict(
        complete, text=empty_text, pics=[], stat_like=99999,
        _matched_keywords=["版本预告"], author_name="更新的账号名",
    )
    stats = await ingest_items(session, "bilibili_dynamics", [empty])

    assert stats.errors == []
    assert stats.items_updated == 0
    assert stats.touched_content_ids == []
    assert content.content_hash == original_hash
    assert content.raw_json == complete
    assert content.last_seen_at > previous_seen
    assert set(content.matched_keywords) == {"鸣潮", "版本预告"}
    assert (await session.scalar(select(Source))).name == "更新的账号名"
    assert await session.scalar(select(func.count(ContentVersion.id))) == 1
    latest = await session.scalar(select(ContentVersion))
    assert latest.normalized_json["media"] == complete_fields["pics"]
    assert latest.text == (complete_fields["text"] or None)
    snapshots = list(await session.scalars(select(MetricSnapshot).order_by(MetricSnapshot.id)))
    assert len(snapshots) == 2
    assert snapshots[-1].metrics["like"] == 99999
    keyword_stat = await session.scalar(
        select(ContentDailyStat).where(ContentDailyStat.keyword == "版本预告")
    )
    assert keyword_stat.contents_new == 1

    # 后续拿到同一份完整内容时，也不应出现空/完整交替新增版本。
    recovered = await ingest_items(session, "bilibili_dynamics", [complete])
    assert recovered.items_updated == 0
    assert await session.scalar(select(func.count(ContentVersion.id))) == 1


@pytest.mark.parametrize(
    "changed_fields",
    [
        {"text": "修改后的正文", "pics": []},
        {"text": "", "pics": ["https://example.com/b.jpg"]},
    ],
)
async def test_ingest_dynamic_real_edits_still_create_unmerged_versions(session, changed_fields):
    complete = dict(DYNAMIC_RAW, text="原正文", pics=["https://example.com/a.jpg"])
    await ingest_items(session, "bilibili_dynamics", [complete])
    content = await session.scalar(select(RawContent))
    original_hash = content.content_hash

    stats = await ingest_items(session, "bilibili_dynamics", [dict(complete, **changed_fields)])

    assert stats.errors == []
    assert stats.items_updated == 1
    assert content.content_hash != original_hash
    assert content.raw_json == complete
    versions = list(await session.scalars(select(ContentVersion).order_by(ContentVersion.version)))
    assert [version.version for version in versions] == [1, 2]
    assert versions[-1].text == (changed_fields["text"] or None)
    assert versions[-1].normalized_json["media"] == changed_fields["pics"]


async def test_ingest_empty_video_edit_is_not_blocked_by_dynamic_guard(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])

    stats = await ingest_items(
        session, "bilibili_search", [dict(VIDEO_RAW, description="", cover_url="")]
    )

    assert stats.items_updated == 1
    latest = await session.scalar(select(ContentVersion).order_by(ContentVersion.version.desc()))
    assert latest.text is None
    assert latest.normalized_json["media"] == []


async def test_reparse_empty_original_dynamic_preserves_later_enrichment(session):
    from app.services.ingest import reparse_contents

    await ingest_items(session, "bilibili_dynamics", [DYNAMIC_RAW])
    await ingest_items(
        session, "bilibili_dynamics", [dict(DYNAMIC_RAW, text="后来补全的正文")]
    )
    content = await session.scalar(select(RawContent))
    complete_hash = content.content_hash

    result = await reparse_contents(session, "bilibili", "dynamic")

    assert result == {"checked": 1, "updated": 0, "failed": 0}
    assert content.content_hash == complete_hash
    assert content.raw_json == DYNAMIC_RAW
    versions = list(await session.scalars(select(ContentVersion).order_by(ContentVersion.version)))
    assert len(versions) == 2
    assert versions[-1].text == "后来补全的正文"


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


async def test_reparse_upgrades_versions(session):
    """parser 升级回填：重算后指纹变化的内容获得新版本。"""
    from sqlalchemy import func, select

    from app.models import ContentVersion, RawContent
    from app.services.ingest import reparse_contents

    # 先用"无标题版"数据入库（模拟旧 parser 产物）
    old_dynamic = dict(DYNAMIC_RAW, text="#鸣潮# #鸣潮3.6版本#\n剧情动画 | 仙尘\n正文第二行")
    await ingest_items(session, "bilibili_dynamics", [old_dynamic])
    raw = await session.scalar(
        select(RawContent).where(RawContent.content_type == "dynamic")
    )
    v1 = await session.scalar(
        select(ContentVersion).where(ContentVersion.raw_content_id == raw.id)
    )
    assert v1.version == 1
    # 模拟旧 parser（v1，无标题推导）的指纹：手工改掉，重算后应产生 v2
    raw.content_hash = "0" * 64
    await session.commit()
    result = await reparse_contents(session, "bilibili", "dynamic")
    assert result["checked"] == 1
    assert result["updated"] == 1
    versions = (
        await session.scalars(
            select(ContentVersion)
            .where(ContentVersion.raw_content_id == raw.id)
            .order_by(ContentVersion.version)
        )
    ).all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].title == "剧情动画 | 仙尘"
    from app.parsers.bilibili import PARSER_VERSION

    assert versions[1].parser_version == PARSER_VERSION
    # 幂等：再次重算指纹不变
    result2 = await reparse_contents(session, "bilibili", "dynamic")
    assert result2["updated"] == 0
    assert await session.scalar(
        select(func.count(ContentVersion.id)).where(ContentVersion.raw_content_id == raw.id)
    ) == 2


async def test_ingest_stops_versioning_when_source_flips_between_bodies(session):
    """RSS 推广位轮播、动态详情时有时无：循环的正文不再产生新版本，也不重新隐藏已发布内容。"""
    base = dict(DYNAMIC_RAW, pics=[])
    for text in ("正文 A", "正文 B", "正文 A", "正文 B", "正文 A", "正文 B"):
        await ingest_items(session, "bilibili_dynamics", [dict(base, text=text)])
    versions = list(await session.scalars(select(ContentVersion).order_by(ContentVersion.version)))
    assert [row.text for row in versions] == ["正文 A", "正文 B", "正文 A"]

    stats = await ingest_items(session, "bilibili_dynamics", [dict(base, text="正文 C")])
    assert stats.items_updated == 1


async def test_ingest_keeps_a_single_genuine_revert(session):
    """来源改完又改回只发生一次时仍如实记录，防抖不能吞掉真实修订。"""
    base = dict(DYNAMIC_RAW, pics=[])
    for text in ("原文", "误发的修改", "原文"):
        await ingest_items(session, "bilibili_dynamics", [dict(base, text=text)])
    versions = list(await session.scalars(select(ContentVersion).order_by(ContentVersion.version)))
    assert [row.text for row in versions] == ["原文", "误发的修改", "原文"]


async def test_ingest_ignores_bilibili_cdn_shard_and_scheme_changes(session):
    """同一张图换 CDN 分片或协议不是内容更新；指纹要跟上，否则每轮都要重新比对。"""
    base = dict(DYNAMIC_RAW, text="正文", pics=["http://i0.hdslb.com/bfs/new_dyn/a.jpg"])
    await ingest_items(session, "bilibili_dynamics", [base])
    content = await session.scalar(select(RawContent))
    before = content.content_hash

    stats = await ingest_items(session, "bilibili_dynamics", [dict(base, pics=["https://i2.hdslb.com/bfs/new_dyn/a.jpg"])])

    assert stats.items_updated == 0
    assert await session.scalar(select(func.count()).select_from(ContentVersion)) == 1
    assert content.content_hash != before


async def test_ingest_still_versions_a_genuinely_different_image(session):
    base = dict(DYNAMIC_RAW, text="正文", pics=["https://i0.hdslb.com/bfs/new_dyn/a.jpg"])
    await ingest_items(session, "bilibili_dynamics", [base])
    stats = await ingest_items(session, "bilibili_dynamics", [dict(base, pics=["https://i0.hdslb.com/bfs/new_dyn/b.jpg"])])
    assert stats.items_updated == 1
    assert await session.scalar(select(func.count()).select_from(ContentVersion)) == 2


async def test_truncation_state_still_creates_a_version_for_rereview():
    """X 正文补全要重新审核，比较键不能把截断差异吞掉。"""
    from app.services.fingerprint import content_body

    assert content_body("t", "正文", [], {"truncated": True}) != content_body("t", "正文", [], {})


async def test_identical_metrics_are_recorded_once_until_the_heartbeat(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    assert await session.scalar(select(func.count()).select_from(MetricSnapshot)) == 1

    snapshot = await session.scalar(select(MetricSnapshot))
    snapshot.captured_at = utcnow() - SNAPSHOT_HEARTBEAT - timedelta(minutes=1)
    await session.commit()
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    assert await session.scalar(select(func.count()).select_from(MetricSnapshot)) == 2


async def test_changed_metrics_are_always_recorded(session):
    await ingest_items(session, "bilibili_search", [VIDEO_RAW])
    await ingest_items(session, "bilibili_search", [dict(VIDEO_RAW, view_count=9999)])
    snapshots = list(await session.scalars(select(MetricSnapshot).order_by(MetricSnapshot.id)))
    assert len(snapshots) == 2 and snapshots[-1].metrics["view"] == 9999
