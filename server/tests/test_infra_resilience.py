"""基础设施加固单测（#5 + #6）：AI 连接池/Redis 共享冷却/备用降级/任务分层/日预算熔断、流水表瘦身、分区锁增量聚合与快慢双队列。"""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import (
    AiUsage,
    BotDelivery,
    BotTarget,
    CollectRun,
    CollectTarget,
    ContentVersion,
    Event,
    Evidence,
    NotificationDelivery,
    Product,
    RawContent,
    RunStatus,
    SiteSetting,
    Source,
    User,
)
from app.services import ai as ai_module
from app.services.events import acquire_aggregation_lock, run_aggregation
from app.services.queue import AI_QUEUE_NAME, ArqQueue, FAST_QUEUE_NAME, enqueue_review_jobs
from app.services.retention import prune_operational_logs
from app.worker.settings import AiWorkerSettings, FastWorkerSettings, WorkerSettings


class _FakeCoolingRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def exists(self, key: str) -> int:
        return int(key in self.store)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)


async def test_ai_redis_shared_cooldown_and_fallback_provider(monkeypatch):
    ai_module._KEY_COOLDOWN.clear()
    fake_redis = _FakeCoolingRedis()
    ai_module.register_ai_redis(fake_redis)
    try:
        # 模拟另一 worker 进程已将 key1 标记为 Redis 共享冷却
        await fake_redis.set(ai_module._cooldown_redis_key("key1"), "1", ex=60)

        seen_calls: list[tuple[str, str, str]] = []

        async def fake_once(cfg, api_key, system, user, *, json_mode=False):
            seen_calls.append((cfg["base_url"], cfg["model"], api_key))
            if api_key == "key2":
                req = httpx.Request("POST", f"{cfg['base_url']}/chat/completions")
                resp = httpx.Response(429, request=req)
                raise httpx.HTTPStatusError("rate limited", request=req, response=resp)
            return "fallback-ok", {"prompt_tokens": 10, "completion_tokens": 5}

        monkeypatch.setattr(ai_module, "_chat_once", fake_once)
        ai_module._POOL_INDEX = 0
        cfg = {
            "base_url": "https://primary.example/v1",
            "api_key": "key1",
            "api_keys": ["key1", "key2"],
            "model": "primary-model",
            "task_models": {"review_content": "cheap-review-model"},
            "fallback_base_url": "https://backup.example/v1",
            "fallback_api_key": "fb-key",
            "fallback_model": "backup-model",
        }
        reply, usage = await ai_module.chat(cfg, "sys", "hello", task="review_content")
        assert reply == "fallback-ok"
        assert usage == {"prompt_tokens": 10, "completion_tokens": 5}
        # key1 被 Redis 冷却跳过；key2 使用任务覆盖模型 cheap-review-model 后 429；随后自动降级到备用通道
        assert seen_calls == [
            ("https://primary.example/v1", "cheap-review-model", "key2"),
            ("https://backup.example/v1", "backup-model", "fb-key"),
        ]
        assert cfg["_last_model"] == "backup-model"
        assert await fake_redis.exists(ai_module._cooldown_redis_key("key2")) == 1
    finally:
        ai_module.register_ai_redis(None)
        ai_module._KEY_COOLDOWN.clear()


async def test_ai_daily_budget_circuit_breaker_and_admin_config(client, session, admin_headers):
    ai_module._invalidate_budget_cache()
    resp = await client.put(
        "/api/admin/ai/config",
        headers=admin_headers,
        json={
            "base_url": "https://primary.example/v1",
            "api_key": "sk-primary-123456",
            "model": "main-model",
            "price_in": 1.0,
            "price_out": 2.0,
            "fallback_base_url": "https://backup.example/v1",
            "fallback_api_key": "sk-backup-999999",
            "fallback_model": "backup-flash",
            "task_models": {"review_content": "mini-flash", "unknown_task": "ignored"},
            "daily_token_limit": 1000,
            "daily_cost_limit": 5.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback_base_url"] == "https://backup.example/v1"
    assert body["fallback_model"] == "backup-flash"
    assert body["fallback_api_key_set"] is True
    assert "sk-backup" not in str(body)
    assert body["task_models"] == {"review_content": "mini-flash"}
    assert body["daily_token_limit"] == 1000

    # 拒绝非 http(s) 的备用地址
    bad = await client.put(
        "/api/admin/ai/config",
        headers=admin_headers,
        json={"base_url": "https://primary.example/v1", "model": "main-model", "fallback_base_url": "javascript:alert(1)"},
    )
    assert bad.status_code == 400

    cfg = await ai_module.get_ai_config(session)
    assert await ai_module.check_ai_budget(session, cfg) is True

    # 今日消耗超过 1000 tokens -> 熔断触发
    session.add(
        AiUsage(
            task="review_content",
            model="mini-flash",
            prompt_tokens=800,
            completion_tokens=400,
            total_tokens=1200,
            cost=0.0016,
            duration_ms=300,
            status="ok",
            created_at=utcnow(),
        )
    )
    await session.commit()
    ai_module._invalidate_budget_cache()
    assert await ai_module.check_ai_budget(session, cfg) is False

    # 后台翻译任务应返回 deferred 而不发送上游请求
    src = Source(platform="youtube", external_id="yt-b", name="YT", identity="official")
    session.add(src)
    await session.flush()
    raw = RawContent(
        platform="youtube",
        external_id="yt-vid-1",
        content_type="video",
        community="wuwa",
        source_id=src.id,
        collected_at=utcnow(),
    )
    session.add(raw)
    await session.flush()
    ver = ContentVersion(raw_content_id=raw.id, version=1, title="Patch Notes", text="Hello world")
    session.add(ver)
    await session.commit()
    tr = await ai_module.translate_content(session, raw.id)
    assert tr == {"status": "deferred", "reason": "daily_budget_exceeded"}

    # 用量接口返回 budget 熔断状态与分任务失败计数
    usage_resp = await client.get("/api/admin/ai/usage", headers=admin_headers, params={"days": 7})
    assert usage_resp.status_code == 200
    usage_body = usage_resp.json()
    assert usage_body["budget"]["tripped"] is True
    assert usage_body["budget"]["today_tokens"] == 1200
    assert usage_body["by_task"][0]["failed"] == 0


async def test_operational_log_retention_prunes_stale_keeps_active(session):
    now = utcnow()
    target = CollectTarget(
        platform="bilibili",
        target_type="keyword_search",
        target_value="鸣潮",
        community="wuwa",
    )
    user = User(email="ret@t.com", password_hash="x", is_verified=True)
    bot_target = BotTarget(name="t", umo="qq:GroupMessage:1", enabled=True, config={})
    session.add_all([target, user, bot_target])
    await session.flush()

    session.add_all([
        # 旧已完成 CollectRun (>30d) -> 删
        CollectRun(target_id=target.id, status=RunStatus.SUCCESS, started_at=now - timedelta(days=40)),
        # 旧运行中 CollectRun -> 保留
        CollectRun(target_id=target.id, status=RunStatus.RUNNING, started_at=now - timedelta(days=40)),
        # 近期 CollectRun -> 保留
        CollectRun(target_id=target.id, status=RunStatus.SUCCESS, started_at=now - timedelta(days=5)),
        # 旧 AiUsage (>90d) -> 删；近期 -> 保留
        AiUsage(task="review_content", model="m", total_tokens=10, created_at=now - timedelta(days=100)),
        AiUsage(task="review_content", model="m", total_tokens=10, created_at=now - timedelta(days=20)),
        # 旧已完成 BotDelivery (>90d) -> 删；pending 保留
        BotDelivery(target_id=bot_target.id, kind="digest", body="old", dedupe_key="k1", status="sent", created_at=now - timedelta(days=100)),
        BotDelivery(target_id=bot_target.id, kind="digest", body="pend", dedupe_key="k2", status="pending", created_at=now - timedelta(days=100)),
        # 旧已完成 NotificationDelivery (>90d) -> 删；近期保留
        NotificationDelivery(user_id=user.id, kind="instant", source="official", period_date=date(2026, 1, 1), dedupe_key="n1", status="sent", created_at=now - timedelta(days=100)),
        NotificationDelivery(user_id=user.id, kind="instant", source="official", period_date=date(2026, 11, 20), dedupe_key="n2", status="sent", created_at=now - timedelta(days=5)),
    ])
    await session.commit()

    stats = await prune_operational_logs(session, now=now)
    assert stats == {
        "collect_runs_removed": 1,
        "custom_runs_removed": 0,
        "ai_usage_removed": 1,
        "deliveries_removed": 2,
    }
    assert len((await session.execute(select(CollectRun))).scalars().all()) == 2
    assert len((await session.execute(select(AiUsage))).scalars().all()) == 1
    assert len((await session.execute(select(BotDelivery))).scalars().all()) == 1
    assert len((await session.execute(select(NotificationDelivery))).scalars().all()) == 1


async def test_partition_lock_and_incremental_aggregation_preserves_old_events_and_watching_evidences(session):
    now = utcnow()
    # 1. 分区锁只创建对应分区键
    await acquire_aggregation_lock(session, communities={"wuwa"})
    await session.commit()
    lock_keys = set(
        (await session.execute(select(SiteSetting.key).where(SiteSetting.key.like("event_aggregation_lock:%")))).scalars()
    )
    assert lock_keys == {"event_aggregation_lock:wuwa"}

    # 2. 构造 90 天前的官方老事件 + 100 天前在监商品的历史证据 + 今日新官方公告
    official_src = Source(platform="bilibili", external_id="off-inc", name="鸣潮官方", identity="official")
    session.add(official_src)
    await session.flush()

    async def _add_raw(ext_id: str, title: str, text: str, category: str, age_days: int) -> RawContent:
        ts = now - timedelta(days=age_days)
        raw = RawContent(
            platform="bilibili",
            external_id=ext_id,
            content_type="dynamic",
            community="wuwa",
            source_id=official_src.id,
            published_at=ts,
            collected_at=ts,
        )
        session.add(raw)
        await session.flush()
        ver = ContentVersion(raw_content_id=raw.id, version=1, title=title, text=text, category=category, parsed_at=ts)
        session.add(ver)
        await session.flush()
        raw.latest_version_id = ver.id
        return raw

    old_official = await _add_raw("old-off-1", "90天前老版本公告", "历史正文", "official_announce", 90)
    old_merch_raw = await _add_raw("old-merch-1", "长离限定手办开启预售", "长离手办预售", "presale", 100)
    watching_prod = Product(
        slug="changli-fig",
        name="长离限定手办",
        community="wuwa",
        watch_terms=["长离手办"],
        monitor_status="watching",
    )
    archived_prod = Product(
        slug="old-archived",
        name="已归档徽章",
        community="wuwa",
        watch_terms=["已归档徽章"],
        monitor_status="archived",
    )
    session.add_all([watching_prod, archived_prod])
    await session.flush()

    # 先全量聚合建立基线
    await run_aggregation(session, communities={"wuwa"}, incremental=False)
    await session.commit()

    old_event = await session.scalar(select(Event).where(Event.title == "90天前老版本公告"))
    assert old_event is not None and old_event.is_active is True
    merch_event = await session.scalar(select(Event).where(Event.product_id == watching_prod.id))
    assert merch_event is not None and merch_event.is_active is True

    # 新增一条今天的官方公告，并跑增量聚合（incremental=True）
    await _add_raw("new-off-1", "今日新版本活动开启", "今天活动正文", "official_announce", 0)
    await session.commit()

    stats = await run_aggregation(session, communities={"wuwa"}, incremental=True)
    await session.commit()
    assert stats.events_created == 1
    assert stats.events_deactivated == 0

    # 90 天前的老官方事件和 100 天前在监商品的历史证据都必须完好保留
    await session.refresh(old_event)
    assert old_event.is_active is True
    await session.refresh(merch_event)
    assert merch_event.is_active is True
    merch_evidences = list(
        await session.scalars(select(Evidence.raw_content_id).where(Evidence.event_id == merch_event.id))
    )
    assert old_merch_raw.id in merch_evidences
    new_event = await session.scalar(select(Event).where(Event.title == "今日新版本活动开启"))
    assert new_event is not None and new_event.is_active is True


async def test_fast_and_ai_worker_queues_partition_and_routing():
    def _fn_names(funcs):
        return {getattr(fn, "name", None) or getattr(fn, "__name__", str(fn)) for fn in funcs}

    fast_names = _fn_names(FastWorkerSettings.functions)
    ai_names = _fn_names(AiWorkerSettings.functions)
    all_names = _fn_names(WorkerSettings.functions)

    # 快慢队列互斥且无遗漏覆盖全部任务
    assert fast_names.isdisjoint(ai_names)
    assert fast_names | ai_names == all_names
    assert FastWorkerSettings.queue_name == FAST_QUEUE_NAME
    assert AiWorkerSettings.queue_name == AI_QUEUE_NAME
    assert "collect_target" in fast_names
    assert "send_instant_notifications" in fast_names
    assert "review_contents" in ai_names
    assert "summarize_event" in ai_names
    assert "extract_entities_sweep" in ai_names

    class _FakePool:
        def __init__(self):
            self.enqueued: list[tuple] = []

        async def enqueue_job(self, name, *args, **kwargs):
            self.enqueued.append((name, args, kwargs))
            return type("Job", (), {"job_id": kwargs.get("_job_id") or name})()

        async def zcard(self, key: str) -> int:
            return {FAST_QUEUE_NAME: 3, AI_QUEUE_NAME: 5}.get(key, 0)

    pool = _FakePool()
    q = ArqQueue(pool)
    await enqueue_review_jobs(pool, [42])
    await q.enqueue_editorial()
    await q.enqueue_ai_batch("summarize_event", [9])
    await q.enqueue_collect(7)

    assert await q.pending_count() == 8
    by_name = {name: kwargs for name, _args, kwargs in pool.enqueued}
    assert by_name["review_contents"]["_queue_name"] == AI_QUEUE_NAME
    assert by_name["news_editorial_sweep"]["_queue_name"] == AI_QUEUE_NAME
    assert by_name["repair_ai_item"]["_queue_name"] == AI_QUEUE_NAME
    assert "_queue_name" not in by_name["collect_target"]
