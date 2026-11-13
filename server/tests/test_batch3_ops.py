"""Batch 3 运维与并发加固单测：单目标采集互斥锁、早报互斥锁、调度器原子续租。"""

import asyncio

import app.services.collect as collect_module
from app.models import CollectRun, CollectTarget
from app.scheduler.loop import _LEADER_KEY, _LEADER_TTL_MS, _is_leader
from app.schemas.content import FetchResult
from app.services.queue import LocalRedis
from app.worker.settings import send_morning_notifications
from sqlalchemy import func, select
from tests.test_parsers_bilibili import VIDEO_RAW


async def test_run_collection_per_target_mutex_prevents_concurrent_reentry(
    session, session_factory, monkeypatch
):
    target = CollectTarget(
        platform="bilibili",
        community="wuwa",
        target_type="keyword_search",
        target_value="鸣潮并发测试",
        interval_minutes=60,
        enabled=True,
    )
    session.add(target)
    await session.commit()

    entered = asyncio.Event()
    proceed = asyncio.Event()
    calls = 0

    class _SlowCollector:
        async def fetch(self, target_type, target_value, limit):
            nonlocal calls
            calls += 1
            entered.set()
            await proceed.wait()
            return FetchResult(platform="bilibili", fetch_kind="bilibili_search", items=[VIDEO_RAW])

    monkeypatch.setattr(collect_module, "SessionLocal", session_factory)
    monkeypatch.setitem(collect_module._COLLECTORS, "bilibili", _SlowCollector)

    redis = LocalRedis()
    first_task = asyncio.create_task(collect_module.run_collection(redis, target.id))
    await entered.wait()

    # 同一目标正在执行时，重入触发立即返回 busy，不新建 CollectRun、不重复请求上游
    second_result = await collect_module.run_collection(redis, target.id)
    assert second_result["status"] == "busy"
    assert second_result["run_id"] is None

    proceed.set()
    first_result = await first_task
    assert first_result["status"] == "success"
    assert calls == 1

    run_count = await session.scalar(select(func.count()).select_from(CollectRun))
    assert run_count == 1

    # 首轮结束后互斥锁已释放，再次手动触发可正常执行
    third_result = await collect_module.run_collection(redis, target.id)
    assert third_result["status"] == "success"
    assert calls == 2


async def test_morning_notifications_worker_mutex_prevents_overlap(session_factory, monkeypatch):
    import app.db as db_module
    import app.services.notifications as notifications_module

    entered = asyncio.Event()
    proceed = asyncio.Event()
    runs = 0

    async def _fake_morning(_session):
        nonlocal runs
        runs += 1
        entered.set()
        await proceed.wait()
        return {"sent": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr(db_module, "SessionLocal", session_factory)
    monkeypatch.setattr(notifications_module, "run_scheduled_morning", _fake_morning)

    redis = LocalRedis()
    ctx = {"redis": redis}
    first = asyncio.create_task(send_morning_notifications(ctx))
    await entered.wait()

    second = await send_morning_notifications(ctx)
    assert second == {"status": "busy"}

    proceed.set()
    assert await first == {"sent": 1, "skipped": 0, "failed": 0}
    assert runs == 1

    # 释放后允许后续重试
    assert await send_morning_notifications(ctx) == {"sent": 1, "skipped": 0, "failed": 0}
    assert runs == 2


async def test_scheduler_leader_atomic_lua_renew_steps_down_when_stolen():
    class _LuaRedis:
        def __init__(self):
            self.value: str | None = None
            self.ttl: int = 0
            self.eval_calls = 0

        async def set(self, key: str, value: str, nx: bool = False, px: int | None = None):
            assert key == _LEADER_KEY
            if nx and self.value is not None:
                return None
            self.value = value
            self.ttl = px or 0
            return True

        async def eval(self, script: str, numkeys: int, key: str, expected: str, ttl_ms: int):
            self.eval_calls += 1
            assert numkeys == 1 and key == _LEADER_KEY
            if self.value == expected:
                self.ttl = ttl_ms
                return 1
            return 0

    shared = _LuaRedis()

    class _ClientProxy:
        def __init__(self, backend: _LuaRedis):
            self._backend = backend

        def __getattr__(self, name: str):
            return getattr(self._backend, name)

    node_a = _ClientProxy(shared)
    node_b = _ClientProxy(shared)

    assert await _is_leader(node_a) is True
    assert await _is_leader(node_b) is False
    # node_a 续租走原子 eval
    assert await _is_leader(node_a) is True
    assert shared.eval_calls >= 2
    assert shared.ttl == _LEADER_TTL_MS

    # 模拟锁过期后被 node_b 抢占，node_a 续租必须原子失败并让位
    shared.value = None
    assert await _is_leader(node_b) is True
    assert await _is_leader(node_a) is False
