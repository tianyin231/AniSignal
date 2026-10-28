"""采集编排单测：collector 打桩，验证 run 记录、健康更新与失败退避。"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

import app.services.collect as collect_module
from app.clock import utcnow
from app.collectors.base import CollectorDeferred
from app.models import CollectionDailyStat, CollectRun, CollectTarget, RawContent, RunStatus
from app.schemas.content import FetchResult
from app.services.statistics import beijing_date

from tests.test_parsers_bilibili import VIDEO_RAW


class _FakeRedis:
    """platform_slot 用到的 SET NX PX / PTTL。"""

    store: dict[str, str] = {}

    async def set(self, key, value, nx=False, px=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def pttl(self, key):
        return 0  # 测试不等礼貌间隔


class _StubCollector:
    calls = 0
    fail = False

    async def fetch(self, target_type, target_value, limit):
        _StubCollector.calls += 1
        if _StubCollector.fail:
            raise collect_module.CollectorError("stub failure")
        return FetchResult(platform="bilibili", fetch_kind="bilibili_search", items=[VIDEO_RAW])


def test_linked_product_filters_search_noise_without_splitting_search_terms():
    target = CollectTarget(platform="bilibili", community="wuwa", target_type="keyword_search", target_value="爱弥斯 联名键盘", tier="core", min_views=0)
    items = [
        {"title": "爱弥斯键盘品控拉完了"},
        {"title": "爱弥斯 联名－机械键盘开箱"},
        {"title": "爱弥斯人物传记"},
        {"title": "绝区零联动机械键盘"},
    ]
    kept, filtered = collect_module.filter_relevant(items, target, product_watch_terms=["爱弥斯键盘", "爱弥斯联名机械键盘"])
    assert [item["title"] for item in kept] == [items[0]["title"], items[1]["title"]]
    assert filtered == 2
    # 未绑定商品的普通目标维持现有筛选语义。
    assert collect_module.filter_relevant(items, target)[1] == 0


@pytest.mark.parametrize("community,term,title", [
    ("wuwa", "鸣潮", "Wuthering Waves NEW UPDATE"),
    ("blhx", "碧蓝航线", "Azur Lane New Ship"),
    ("hsr", "星穹铁道", "Honkai: Star Rail 4.0"),
    ("hsr", "星穹铁道", "ＨＳＲ 新角色"),
    ("arknights", "明日方舟", "Arknights Update"),
])
def test_youtube_aliases_keep_only_one_game_community(community, term, title):
    target = CollectTarget(platform="youtube", community=community, target_type="channel_rss", target_value="channel", co_terms=[term], min_views=0)
    kept, filtered = collect_module.filter_relevant([{"title": title}], target)
    assert len(kept) == 1 and filtered == 0


@pytest.mark.parametrize("community,term,title", [
    ("wuwa", "鸣潮", "Wuthering Waves vs Honkai Star Rail"),
    ("hsr", "星穹铁道", "Wuthering Waves vs HSR"),
    ("hsr", "星穹铁道", "ABC_HSRLONG Update"),
    ("arknights", "明日方舟", "Arknights: Endfield Update"),
    ("blhx", "碧蓝航线", "Wuthering Waves New Update"),
])
def test_youtube_ambiguous_other_games_and_partial_abbreviations_are_filtered(community, term, title):
    target = CollectTarget(platform="youtube", community=community, target_type="channel_rss", target_value="channel", co_terms=[term], min_views=0)
    kept, filtered = collect_module.filter_relevant([{"title": title, "summary": term}], target)
    assert kept == [] and filtered == 1


def test_official_youtube_source_keeps_explicit_source_ownership():
    target = CollectTarget(platform="youtube", community="wuwa", target_type="channel_rss", target_value="official", co_terms=[], min_views=0)
    assert collect_module.filter_relevant([{"title": "Wuthering Waves x HSR"}], target)[1] == 0


@pytest.fixture
def patched_collect(monkeypatch, session_factory):
    _StubCollector.fail = False
    monkeypatch.setattr(collect_module, "_COLLECTORS", {"bilibili": _StubCollector})
    monkeypatch.setattr(collect_module, "SessionLocal", session_factory)
    _FakeRedis.store.clear()


async def _make_target(session) -> CollectTarget:
    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value="鸣潮",
        interval_minutes=30,
        enabled=True,
        next_run_at=utcnow(),
    )
    session.add(target)
    await session.commit()
    return target


async def test_run_collection_success(session, patched_collect):
    target = await _make_target(session)
    result = await collect_module.run_collection(_FakeRedis(), target.id)

    assert result["status"] == "success"
    assert result["items_new"] == 1

    run = await session.get(CollectRun, result["run_id"])
    assert run.status == RunStatus.SUCCESS
    assert run.items_found == 1
    assert run.items_fetched == 1
    assert run.items_filtered == 0
    assert run.duration_ms is not None

    daily = await session.get(
        CollectionDailyStat,
        {"stat_date": beijing_date(collect_module.utcnow()), "target_id": target.id, "status": "success"},
    )
    assert daily is not None
    assert daily.runs == 1
    assert daily.detailed_runs == 1
    assert daily.items_new == 1

    await session.refresh(target)
    assert target.last_success_at is not None
    assert target.consecutive_failures == 0
    assert target.last_error is None


async def test_run_collection_applies_explicit_product_binding(session, patched_collect, monkeypatch):
    from app.models import Product

    target = await _make_target(session)
    target.tier = "core"
    target.min_views = 0
    session.add(Product(slug="keyboard", name="爱弥斯键盘", community="wuwa", watch_terms=["爱弥斯键盘"], monitor_target_id=target.id))
    await session.commit()

    async def fetch(*args):
        return FetchResult(platform="bilibili", fetch_kind="bilibili_search", items=[
            dict(VIDEO_RAW, bvid="good", title="爱弥斯键盘品控拉完了", description=""),
            dict(VIDEO_RAW, bvid="noise", title="爱弥斯人物传记", description=""),
        ])

    monkeypatch.setattr(_StubCollector, "fetch", fetch)
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    run = await session.get(CollectRun, result["run_id"])
    assert run.items_fetched == 2 and run.items_filtered == 1 and run.items_new == 1
    assert (await session.scalars(select(RawContent.external_id))).all() == ["good"]


async def test_youtube_existing_other_community_content_is_not_updated(session, patched_collect, monkeypatch):
    from app.services.ingest import ingest_items

    old = {"id": "shared-video", "title": "Wuthering Waves update", "channel_id": "shared", "summary": ""}
    await ingest_items(session, "youtube_rss", [old], community="wuwa")
    target = CollectTarget(platform="youtube", community="hsr", target_type="channel_rss", target_value="shared", co_terms=["星穹铁道"], min_views=0)
    session.add(target)
    await session.commit()

    class YoutubeStub:
        async def fetch(self, *args):
            return FetchResult(platform="youtube", fetch_kind="youtube_rss", items=[dict(old, title="Honkai Star Rail update")])

    monkeypatch.setattr(collect_module, "_COLLECTORS", {"youtube": YoutubeStub})
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    run = await session.get(CollectRun, result["run_id"])
    raw = await session.scalar(select(RawContent))
    assert run.items_fetched == 1 and run.items_filtered == 1 and run.items_updated == 0
    assert raw.community == "wuwa" and raw.raw_json["title"] == old["title"]


async def test_merch_collection_filters_foreign_game_and_preserves_official_identity(session, patched_collect, monkeypatch):
    from app.models import Source
    from tests.test_parsers_bilibili import DYNAMIC_RAW

    target = await _make_target(session)
    target.target_type, target.target_value, target.tier = "account_dynamics", "3493082584582776", "core"
    await session.commit()

    async def fetch(*args):
        return FetchResult(platform="bilibili", fetch_kind="bilibili_dynamics", items=[
            dict(DYNAMIC_RAW, id="merch-wuwa", author_mid=3493082584582776, author_name="库洛游戏周边",
                 type="DYNAMIC_TYPE_DRAW", text="#鸣潮# 主题手办预售", pics=["https://example.com/figure.jpg"]),
            dict(DYNAMIC_RAW, id="merch-other", author_mid=3493082584582776, text="#战双帕弥什# 手办预售"),
        ])

    monkeypatch.setattr(_StubCollector, "fetch", fetch)
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    run = await session.get(CollectRun, result["run_id"])
    raw = await session.scalar(select(RawContent))
    source = await session.get(Source, raw.source_id)
    assert run.items_fetched == 2 and run.items_filtered == 1 and run.items_new == 1
    assert run.official_items == 1 and source.identity == "official"
    assert raw.external_id == "merch-wuwa" and raw.community == "wuwa"


async def test_run_collection_failure_updates_health(session, patched_collect):
    target = await _make_target(session)
    _StubCollector.fail = True

    result = await collect_module.run_collection(_FakeRedis(), target.id)
    assert result["status"] == "failed"
    assert "stub failure" in result["error"]

    await session.refresh(target)
    assert target.consecutive_failures == 1
    assert target.last_success_at is None
    assert "stub failure" in target.last_error


async def test_failure_backoff_doubles_interval(session, patched_collect):
    target = await _make_target(session)
    target.consecutive_failures = 2
    await session.commit()

    now = utcnow()
    nxt = collect_module.next_run_with_jitter(target, now)
    # 2 次失败 → 4 倍间隔（30min * 4 = 120min），抖动 ±10%
    assert timedelta(minutes=100) < (nxt - now) < timedelta(minutes=140)


@pytest.mark.parametrize("interval,failures,error,expected", [
    (15, 1, "BiliRiskControlError: refused", 30),
    (15, 7, "BiliRiskControlError: refused", 60),
    (30, 9, "BiliRiskControlError: refused", 60),
    (120, 9, "BiliRiskControlError: refused", 120),
    (30, 9, "CollectorError: timeout", 960),
])
def test_dynamic_risk_control_does_not_miss_recovery_for_hours(monkeypatch, interval, failures, error, expected):
    monkeypatch.setattr(collect_module.random, "uniform", lambda *_: 1.0)
    target = CollectTarget(platform="bilibili", target_type="account_dynamics", target_value="1955897084",
        interval_minutes=interval, consecutive_failures=failures, last_error=error)
    now = utcnow()
    assert collect_module.next_run_with_jitter(target, now) - now == timedelta(minutes=expected)


async def test_dynamic_refusal_keeps_failure_visible_but_probes_recovery_within_an_hour(session, patched_collect, monkeypatch):
    from app.collectors.bilibili import BiliRiskControlError

    target = await _make_target(session)
    target.target_type = "account_dynamics"
    target.target_value = "1955897084"
    target.consecutive_failures = 9
    await session.commit()

    async def refused(*args):
        raise BiliRiskControlError("the first page of this feed carried nothing")

    monkeypatch.setattr(_StubCollector, "fetch", refused)
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    await session.refresh(target)
    assert result["status"] == "failed"
    assert target.consecutive_failures == 10 and target.last_success_at is None
    assert timedelta(minutes=54) <= target.next_run_at - target.last_run_at <= timedelta(minutes=66)


async def test_success_restores_interval_after_failure_backoff(session, patched_collect):
    target = await _make_target(session)
    target.consecutive_failures = 5
    target.next_run_at = utcnow() + timedelta(hours=16)
    await session.commit()

    await collect_module.run_collection(_FakeRedis(), target.id)
    await session.refresh(target)

    assert target.consecutive_failures == 0
    assert timedelta(minutes=27) <= target.next_run_at - target.last_run_at <= timedelta(minutes=33)


async def test_failure_reschedules_using_current_failure_count(session, patched_collect):
    target = await _make_target(session)
    _StubCollector.fail = True
    await collect_module.run_collection(_FakeRedis(), target.id)
    await session.refresh(target)
    assert timedelta(minutes=54) <= target.next_run_at - target.last_run_at <= timedelta(minutes=66)


async def test_circuit_cooldown_is_skipped_without_increasing_failures(session, patched_collect, monkeypatch):
    target = await _make_target(session)
    target.consecutive_failures = 2
    target.last_error = "previous actual refusal"
    await session.commit()
    retry_at = utcnow() + timedelta(minutes=45)

    async def deferred(*args):
        raise CollectorDeferred("platform cooling down", retry_at=retry_at)

    monkeypatch.setattr(_StubCollector, "fetch", deferred)
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    await session.refresh(target)
    run = await session.get(CollectRun, result["run_id"])
    assert run.status == "skipped"
    assert run.error == "platform cooling down"
    assert target.consecutive_failures == 2
    assert target.last_error == "previous actual refusal"
    assert target.last_success_at is None
    assert target.next_run_at == result["retry_at"] == retry_at


@pytest.mark.parametrize("all_invalid", [True, False])
async def test_parse_failure_reports_failed_or_partial(session, patched_collect, monkeypatch, all_invalid):
    target = await _make_target(session)

    async def fetch(*args):
        items = [{}] if all_invalid else [VIDEO_RAW, {}]
        return FetchResult(platform="bilibili", fetch_kind="bilibili_search", items=items)

    monkeypatch.setattr(_StubCollector, "fetch", fetch)
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    await session.refresh(target)
    run = await session.get(CollectRun, result["run_id"])
    assert run.status == ("failed" if all_invalid else "partial")
    assert run.parse_failed == 1
    assert "parse failed" in run.error
    assert target.consecutive_failures == (1 if all_invalid else 0)
    assert (target.last_success_at is None) == all_invalid


@pytest.mark.parametrize("database_error", [False, True])
async def test_postprocessing_failure_preserves_ingestion_and_records_partial(
    session, patched_collect, monkeypatch, database_error,
):
    from app.services import events

    target = await _make_target(session)

    async def fail_aggregation(collect_session):
        if database_error:
            collect_session.add(CollectTarget(
                platform=target.platform, target_type=target.target_type,
                target_value=target.target_value,
            ))
            await collect_session.flush()
        raise RuntimeError("aggregation unavailable")

    monkeypatch.setattr(events, "run_aggregation", fail_aggregation)
    result = await collect_module.run_collection(_FakeRedis(), target.id)
    await session.refresh(target)
    run = await session.get(CollectRun, result["run_id"])
    assert run.status == "partial"
    assert run.error == result["error"]
    assert run.error
    assert target.last_error == run.error
    assert target.last_success_at is not None
    assert await session.scalar(select(func.count()).select_from(RawContent)) == 1


async def test_statistics_excludes_skips_and_counts_only_full_success(session):
    from app.services.statistics import build_statistics, record_collection_stat

    target = await _make_target(session)
    for status in (RunStatus.SUCCESS, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.SKIPPED):
        run = CollectRun(
            target_id=target.id, status=status, started_at=utcnow(), finished_at=utcnow(),
            items_found=0, items_new=0,
        )
        session.add(run)
        await record_collection_stat(session, run)
    await session.commit()

    overview = await build_statistics(session, 7)
    assert overview.totals.collection_runs == 3
    assert overview.totals.failed_runs == 1
    assert overview.totals.success_rate == 33.3
    assert overview.targets[0].runs == 3
    assert any(run.status == "skipped" for run in overview.recent_runs)


async def test_recover_stale_runs_is_idempotent_and_preserves_current_target_health(session):
    target = await _make_target(session)
    target.consecutive_failures = 2
    schedule = target.next_run_at
    old_start = utcnow() - timedelta(days=2)
    stale = CollectRun(target_id=target.id, started_at=old_start, status=RunStatus.RUNNING)
    active = CollectRun(target_id=target.id, started_at=utcnow() - timedelta(minutes=45), status=RunStatus.RUNNING)
    completed = CollectRun(target_id=target.id, started_at=old_start, status=RunStatus.SUCCESS)
    session.add_all([stale, active, completed])
    await session.commit()

    assert await collect_module.recover_stale_runs(session) == 1
    assert await collect_module.recover_stale_runs(session) == 0
    assert stale.status == RunStatus.FAILED
    assert stale.finished_at == old_start + timedelta(minutes=30)
    assert "启动时恢复" in stale.error
    assert active.status == RunStatus.RUNNING
    assert completed.status == RunStatus.SUCCESS
    await session.refresh(target)
    assert target.consecutive_failures == 2
    assert target.next_run_at == schedule
    daily = await session.get(CollectionDailyStat, {
        "stat_date": beijing_date(stale.finished_at), "target_id": target.id, "status": "failed",
    })
    assert daily.runs == 1


def test_community_youtube_rss_filters_by_title_case_insensitively():
    target = CollectTarget(
        platform="youtube",
        target_type="channel_rss",
        target_value="UCcommunity",
        co_terms=["Wuthering Waves", "WuWa"],
    )
    items = [
        {"title": "New WUTHERING WAVES character guide", "summary": "Build notes"},
        {"title": "Another game guide", "summary": "Wuthering Waves and other channel tags"},
    ]
    kept, filtered = collect_module.filter_relevant(items, target)
    assert kept == [items[0]]
    assert filtered == 1
    assert "Wuthering Waves" in kept[0]["_matched_keywords"]


async def test_core_tier_semantics(session):
    """core 级：共现词豁免；min_views=0 全量入库，>0 时作为兜底门槛。"""
    from app.models import CollectTarget

    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value="爱弥斯",
        co_terms=["联名"],
        min_views=0,
        tier="core",
    )
    items = [
        {"title": "随手拍的爱弥斯键盘", "view_count": 120},  # 低播放、无共现词
        {"title": "爱弥斯联名键盘开箱", "view_count": 50000, "_matched_keywords": ["旧标"]},
    ]
    kept, filtered = collect_module.filter_relevant(items, target)
    assert filtered == 0 and len(kept) == 2
    assert "爱弥斯" in kept[0]["_matched_keywords"]
    assert "联名" in kept[1]["_matched_keywords"]

    # min_views > 0 时核心词也走兜底门槛
    target.min_views = 10000
    low = {"title": "随手拍的爱弥斯键盘", "view_count": 120}
    high = {"title": "爱弥斯联名键盘开箱", "view_count": 50000}
    kept, filtered = collect_module.filter_relevant([low, high], target)
    assert [i["view_count"] for i in kept] == [50000]
    assert filtered == 1


async def test_general_tier_keeps_thresholds(session):
    """general 级目标维持热度门槛与共现词过滤。"""
    from app.models import CollectTarget

    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value="鸣潮",
        co_terms=["版本", "共鸣"],
        min_views=10000,
        tier="general",
    )
    items = [
        {"title": "鸣潮日常闲聊", "view_count": 500},  # 双不达标
        {"title": "鸣潮新版本解说", "view_count": 20000},  # 达标
    ]
    kept, filtered = collect_module.filter_relevant(items, target)
    assert filtered == 1
    assert len(kept) == 1
    assert "新版本解说" in kept[0]["title"]


async def test_create_target_with_tier_roundtrip(client, admin_headers):
    """目标分级 core/general 创建与读取。"""
    resp = await client.post(
        "/api/admin/collect-targets",
        json={
            "platform": "bilibili",
            "target_type": "keyword_search",
            "target_value": "爱弥斯",
            "min_views": 0,
            "tier": "core",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["tier"] == "core"
    resp = await client.get("/api/admin/collect-targets", headers=admin_headers)
    assert any(t["tier"] == "core" for t in resp.json())


async def test_backfill_empty_draw_dynamics(session):
    """空 DRAW 动态重试匿名详情补全：成功后以新版本入库且作者信息保留。"""
    from sqlalchemy import select

    from app.collectors.bilibili import BilibiliCollector
    from app.models import ContentVersion, RawContent
    from app.services.collect import _backfill_empty_draw_dynamics
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import DYNAMIC_RAW

    await ingest_items(
        session,
        "bilibili_dynamics",
        [dict(DYNAMIC_RAW, id="1241836553225896086", text="", pics=[])],
    )
    raw = await session.scalar(
        select(RawContent).where(RawContent.external_id == "1241836553225896086")
    )
    assert raw is not None
    author_mid_before = raw.raw_json.get("author_mid")

    class StubCollector:
        async def _fetch_draw_detail(self, dynamic_id):
            return {"text": "补全的动态正文，含现场图", "pics": ["https://example.com/p1.jpg"]}

    backfilled = await _backfill_empty_draw_dynamics(session, StubCollector(), limit=5)
    assert backfilled == 1

    versions = await session.scalars(
        select(ContentVersion)
        .where(ContentVersion.raw_content_id == raw.id)
        .order_by(ContentVersion.version)
    )
    versions = list(versions)
    assert len(versions) == 2  # 空版本 + 补全新版本
    assert "补全的动态正文" in versions[-1].text
    # 作者信息从 raw_json 保留，不会变成 unknown
    assert versions[-1].normalized_json["author"]["external_id"] == str(author_mid_before)


async def test_backfill_only_missing_draw_and_article(session):
    from app.services.collect import _backfill_empty_draw_dynamics
    from app.services.ingest import ingest_items
    from tests.test_parsers_bilibili import DYNAMIC_RAW

    await ingest_items(session, "bilibili_dynamics", [
        dict(DYNAMIC_RAW, id="article", type="DYNAMIC_TYPE_ARTICLE", text="", pics=[]),
        dict(DYNAMIC_RAW, id="live", type="DYNAMIC_TYPE_LIVE_RCMD", text="", pics=[]),
        dict(DYNAMIC_RAW, id="image", type="DYNAMIC_TYPE_DRAW", text="", pics=["https://example.com/a.jpg"]),
    ])
    calls = []

    class StubCollector:
        async def _fetch_draw_detail(self, dynamic_id):
            calls.append(dynamic_id)
            return {"text": "补回专栏", "pics": []}

    assert await _backfill_empty_draw_dynamics(session, StubCollector(), limit=1) == 1
    assert calls == ["article"]
