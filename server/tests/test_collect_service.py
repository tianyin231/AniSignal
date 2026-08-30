"""采集编排单测：collector 打桩，验证 run 记录、健康更新与失败退避。"""

from datetime import timedelta

import pytest

import app.services.collect as collect_module
from app.clock import utcnow
from app.models import CollectionDailyStat, CollectRun, CollectTarget, RunStatus
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


async def test_core_tier_bypasses_thresholds(session):
    """core 级目标全量入库：低播放、不命中共现词的内容都保留（强及时性）。"""
    from app.models import CollectTarget

    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value="爱弥斯",
        co_terms=["联名"],
        min_views=10000,
        tier="core",
    )
    items = [
        {"title": "随手拍的爱弥斯键盘", "view_count": 120},  # 低播放、无共现词
        {"title": "爱弥斯联名键盘开箱", "view_count": 50000, "_matched_keywords": ["旧标"]},
    ]
    kept, filtered = collect_module.filter_relevant(items, target)
    assert filtered == 0
    assert len(kept) == 2
    assert "爱弥斯" in kept[0]["_matched_keywords"]
    # 命中的共现词仍会追加溯源
    assert "联名" in kept[1]["_matched_keywords"]


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
