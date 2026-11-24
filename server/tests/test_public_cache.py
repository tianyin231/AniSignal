"""公开热点 API Redis 缓存单测：命中、内存上限保护、防 Key 爆炸、singleflight 与主动失效。"""

import asyncio
from datetime import timedelta

import pytest

from app.api import main as api_main
from app.clock import utcnow
from app.models import CollectTarget, ContentSchedule, RawContent, RedeemCode
from app.services import public_cache
from app.services.auth import SESSION_COOKIE
from app.services.hidden_communities import HIDDEN_COOKIE
from app.services.public_cache import CACHE_PREFIX, SITE_CONFIG_KEY, cached_dict, register_cache_redis


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, tuple[str, int | None]] = {}
        self.gets = 0
        self.sets = 0
        self.deletes = 0

    async def get(self, key: str):
        self.gets += 1
        item = self.store.get(key)
        return item[0] if item else None

    async def set(self, key: str, value: str, ex: int | None = None):
        self.sets += 1
        self.store[key] = (value, ex)

    async def delete(self, *keys: str):
        self.deletes += 1
        for key in keys:
            self.store.pop(key, None)


@pytest.fixture
def fake_redis():
    redis = _FakeRedis()
    prev = getattr(api_main.app.state, "redis", None)
    api_main.app.state.redis = redis
    register_cache_redis(redis)
    try:
        yield redis
    finally:
        api_main.app.state.redis = prev
        register_cache_redis(None)


async def test_site_config_caches_and_invalidates_on_admin_update(client, admin_headers, fake_redis):
    first = await client.get("/api/site-config")
    assert first.status_code == 200
    assert SITE_CONFIG_KEY in fake_redis.store
    assert fake_redis.store[SITE_CONFIG_KEY][1] == 60
    assert fake_redis.sets == 1

    # 第二次直接命中 Redis，不再重新写入
    second = await client.get("/api/site-config")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert fake_redis.sets == 1

    # 管理端更新公告后，缓存立刻失效，公开侧立即可见新内容
    put = await client.put(
        "/api/admin/settings/announcement",
        headers=admin_headers,
        json={"enabled": True, "level": "info", "text": "维护完成", "link": ""},
    )
    assert put.status_code == 200
    assert SITE_CONFIG_KEY not in fake_redis.store

    refreshed = await client.get("/api/site-config")
    assert refreshed.status_code == 200
    assert refreshed.json()["announcement"]["text"] == "维护完成"
    assert fake_redis.sets == 2


async def test_hot_public_endpoints_cache_default_views_and_skip_high_cardinality_params(
    client, session, fake_redis
):
    now = utcnow()
    raw = RawContent(platform="kuro_cn", content_type="notice", external_id="cache-1", community="wuwa")
    session.add(raw)
    await session.flush()
    session.add(ContentSchedule(
        raw_content_id=raw.id, community="wuwa", title="版本活动", kind="activity",
        start_at=now - timedelta(hours=1), end_at=now + timedelta(days=2),
    ))
    session.add(RedeemCode(
        raw_content_id=raw.id, community="wuwa", code="CACHECODE1", status="active",
        expires_at=now + timedelta(days=1),
    ))
    session.add(CollectTarget(
        platform="kuro_cn", target_type="notices", target_value="default", community="wuwa", enabled=True,
    ))
    await session.commit()

    # 1. 标准公共视角命中缓存
    for path in (
        "/api/trending?community=wuwa&hours=24&limit=5",
        "/api/calendar?community=wuwa",
        "/api/codes?community=wuwa",
        "/api/sources?community=wuwa",
        "/api/events?community=wuwa&limit=6",
        "/api/products?community=wuwa",
        "/api/steam/pulse?community=wuwa&hours=168",
    ):
        r1 = await client.get(path)
        r2 = await client.get(path)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()

    expected_keys = {
        f"{CACHE_PREFIX}trending:wuwa:24:5",
        f"{CACHE_PREFIX}calendar:wuwa",
        f"{CACHE_PREFIX}codes:wuwa",
        f"{CACHE_PREFIX}sources:wuwa",
        f"{CACHE_PREFIX}events:wuwa:6",
        f"{CACHE_PREFIX}products:wuwa",
        f"{CACHE_PREFIX}steam-pulse:wuwa:168",
    }
    assert expected_keys <= set(fake_redis.store)
    assert all((fake_redis.store[k][1] or 0) <= 60 for k in expected_keys)
    sets_before = fake_redis.sets

    # 2. 非白名单参数（任意 hours / 搜索词 q / 深分页 offset）绝不写入 Redis，防 Key 爆炸
    for path in (
        "/api/trending?community=wuwa&hours=17&limit=7",
        "/api/events?community=wuwa&q=test",
        "/api/events?community=wuwa&limit=6&offset=6",
        "/api/steam/pulse?community=wuwa&hours=37",
    ):
        resp = await client.get(path)
        assert resp.status_code == 200
    assert fake_redis.sets == sets_before

    # 3. 带用户屏蔽 cookie 时跳过写入 Redis，防 2^N cookie 组合撑爆内存
    resp = await client.get(
        "/api/highlights?community=all&limit=5",
        headers={"Cookie": f"{SESSION_COOKIE}=sess-token; {HIDDEN_COOKIE}=blhx"},
    )
    assert resp.status_code == 200
    assert fake_redis.sets == sets_before


async def test_oversized_payload_is_not_written_to_redis():
    redis = _FakeRedis()
    big = {"blob": "x" * 2000}

    result = await cached_dict(redis, "pubcache:v1:big", 60, lambda: asyncio.sleep(0, result=big), max_bytes=512)
    assert result == big
    assert "pubcache:v1:big" not in redis.store


async def test_singleflight_coalesces_concurrent_cache_misses():
    redis = _FakeRedis()
    calls = 0

    async def slow_loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return {"ok": True, "calls": calls}

    results = await asyncio.gather(*(
        cached_dict(redis, "pubcache:v1:sf-test", 60, slow_loader)
        for _ in range(6)
    ))
    assert calls == 1
    assert all(r == {"ok": True, "calls": 1} for r in results)
    assert redis.sets == 1


async def test_redis_error_falls_back_gracefully():
    class _BrokenRedis:
        async def get(self, _key):
            raise ConnectionError("redis down")

        async def set(self, _key, _value, ex=None):
            raise ConnectionError("redis down")

    res = await public_cache.cached_dict(
        _BrokenRedis(), "pubcache:v1:broken", 60, lambda: asyncio.sleep(0, result={"alive": True})
    )
    assert res == {"alive": True}
