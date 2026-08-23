"""本地测试设施单测：LocalRedis（礼貌间隔）与 LocalQueue（进程内执行）。"""

import asyncio

import app.services.collect as collect_module
from app.services.queue import LocalQueue, LocalRedis


async def test_local_redis_nx_expiry():
    redis = LocalRedis()
    assert await redis.set("k", "1", nx=True, px=1000) is True
    # 未过期时 NX 抢不到
    assert await redis.set("k", "1", nx=True, px=1000) is None
    ttl = await redis.pttl("k")
    assert 0 < ttl <= 1000


async def test_local_queue_executes_in_process(monkeypatch):
    calls: list = []

    async def fake_run(redis, target_id):
        calls.append((redis, target_id))

    monkeypatch.setattr(collect_module, "run_collection", fake_run)

    queue = LocalQueue()
    job_id = await queue.enqueue_collect(42)
    assert job_id is not None and job_id.startswith("local:42:")
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(calls) == 1
    # 传给 run_collection 的是共享的 LocalRedis（跨任务礼貌间隔的基础）
    assert calls[0][0] is queue.redis
    assert calls[0][1] == 42


async def test_local_queue_task_failure_isolated(monkeypatch):
    async def boom(redis, target_id):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(collect_module, "run_collection", boom)
    queue = LocalQueue()
    assert await queue.enqueue_collect(1) is not None
    for _ in range(10):
        await asyncio.sleep(0)
    # 失败被吞掉（与 arq worker 行为一致），队列可用
    assert await queue.pending_count() == 0
