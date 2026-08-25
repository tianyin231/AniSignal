"""采集编排单测：collector 打桩，验证 run 记录、健康更新与失败退避。"""

from datetime import timedelta

import pytest

import app.services.collect as collect_module
from app.clock import utcnow
from app.models import CollectRun, CollectTarget, RunStatus
from app.schemas.content import FetchResult

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


def test_community_youtube_rss_filters_by_summary_case_insensitively():
    target = CollectTarget(
        platform="youtube",
        target_type="channel_rss",
        target_value="UCcommunity",
        co_terms=["Wuthering Waves", "WuWa"],
    )
    items = [
        {"title": "New character guide", "summary": "A WUTHERING WAVES build"},
        {"title": "Another game guide", "summary": "Unrelated release"},
    ]
    kept, filtered = collect_module.filter_relevant(items, target)
    assert kept == [items[0]]
    assert filtered == 1
    assert "Wuthering Waves" in kept[0]["_matched_keywords"]
