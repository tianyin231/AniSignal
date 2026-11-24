"""公开热点 API 的限容 Redis 缓存（适配 48 MB Redis 内存预算 + volatile-lru）。

设计约束：
1. 仅缓存标准公共视角（无个性化屏蔽 cookie、白名单参数组合），彻底杜绝 Key 基数爆炸；
2. 单条序列化体积超过 MAX_CACHE_BYTES 直接跳过写入，防止超大响应挤占队列/锁空间；
3. 所有缓存 Key 必须设置短 TTL，在 volatile-lru 下优先淘汰，不影响无 TTL 的 arq 队列与锁；
4. 进程内按 Key 做 singleflight（WeakValueDictionary 自动回收锁），避免过期瞬间惊群打库；
5. 任何 Redis 异常透明回源，不中断公开接口服务。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any, TypeVar
import weakref

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CACHE_PREFIX = "pubcache:v1:"
SITE_CONFIG_KEY = f"{CACHE_PREFIX}site-config"
# 写入以下任一配置时，立即失效公开站点配置缓存，避免管理端修改出现延迟。
SITE_CONFIG_SETTINGS = frozenset({
    "ai",
    "smtp",
    "announcement",
    "texts",
    "media_visibility",
    "communities",
})

# 单条缓存体积硬上限（96 KB），避免大响应撑爆 48 MB 内存预算。
MAX_CACHE_BYTES = 96 * 1024

_TModel = TypeVar("_TModel", bound=BaseModel)
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_default_redis: Any = None


def register_cache_redis(redis: Any) -> None:
    """在 API lifespan 启动/关闭时绑定默认 Redis 客户端，供配置写入后自动失效缓存。"""
    global _default_redis
    _default_redis = redis


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def _safe_get(redis: Any, key: str) -> str | bytes | None:
    if redis is None:
        return None
    try:
        return await redis.get(key)
    except Exception:  # noqa: BLE001 - Redis 不可用时降级回源
        return None


async def _safe_set(redis: Any, key: str, payload: str, ttl: int, max_bytes: int) -> None:
    if redis is None or ttl <= 0:
        return
    if len(payload.encode("utf-8")) > max_bytes:
        logger.debug("skip caching oversized payload key=%s max=%s", key, max_bytes)
        return
    try:
        await redis.set(key, payload, ex=ttl)
    except Exception:  # noqa: BLE001
        pass


async def cached_model(
    redis: Any,
    key: str | None,
    ttl: int,
    model_cls: type[_TModel],
    loader: Callable[[], Awaitable[_TModel]],
    *,
    max_bytes: int = MAX_CACHE_BYTES,
) -> _TModel:
    """缓存 Pydantic 模型响应；key 为 None 时直接回源不走缓存。"""
    if redis is None or not key:
        return await loader()

    cached = await _safe_get(redis, key)
    if cached:
        try:
            return model_cls.model_validate_json(cached)
        except Exception:  # noqa: BLE001 - 反序列化异常时回源覆盖
            pass

    async with _lock_for(key):
        cached = await _safe_get(redis, key)
        if cached:
            try:
                return model_cls.model_validate_json(cached)
            except Exception:  # noqa: BLE001
                pass
        result = await loader()
        await _safe_set(redis, key, result.model_dump_json(), ttl, max_bytes)
        return result


async def cached_dict(
    redis: Any,
    key: str | None,
    ttl: int,
    loader: Callable[[], Awaitable[dict[str, Any]]],
    *,
    max_bytes: int = MAX_CACHE_BYTES,
) -> dict[str, Any]:
    """缓存 JSON 字典响应（使用紧凑 JSON 编码节省 Redis 内存）；key 为 None 时直接回源。"""
    if redis is None or not key:
        return await loader()

    cached = await _safe_get(redis, key)
    if cached:
        try:
            parsed = json.loads(cached)
            if isinstance(parsed, dict):
                return parsed
        except Exception:  # noqa: BLE001
            pass

    async with _lock_for(key):
        cached = await _safe_get(redis, key)
        if cached:
            try:
                parsed = json.loads(cached)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:  # noqa: BLE001
                pass
        result = await loader()
        encoded = json.dumps(jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"))
        await _safe_set(redis, key, encoded, ttl, max_bytes)
        return result


async def invalidate_keys(redis: Any, *keys: str) -> None:
    target = redis if redis is not None else _default_redis
    if target is None or not keys:
        return
    try:
        await target.delete(*keys)
    except Exception:  # noqa: BLE001
        pass


async def invalidate_for_setting(setting_key: str, redis: Any = None) -> None:
    if setting_key in SITE_CONFIG_SETTINGS:
        await invalidate_keys(redis, SITE_CONFIG_KEY)
