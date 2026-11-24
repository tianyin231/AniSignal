"""公开热点 API 的限容 Redis 缓存（适配 48 MB Redis 内存预算 + volatile-lru）。

设计约束：
1. 仅缓存标准公共视角（无个性化屏蔽 cookie、白名单参数组合），彻底杜绝 Key 基数爆炸；
2. 对 >= COMPRESS_THRESHOLD_BYTES 的负载透明启用 gzip + base64 压缩（兼容 decode_responses=True），
   并将最终写入字节严格限制在 MAX_CACHE_BYTES 以内，防止大响应挤占队列/锁空间；
3. 所有缓存 Key 必须设置短 TTL，在 volatile-lru 下优先淘汰，不影响无 TTL 的 arq 队列与锁；
4. 进程内按 Key 做 singleflight（WeakValueDictionary 自动回收锁），避免过期瞬间惊群打库；
5. 任何 Redis 异常透明回源，不中断公开接口服务。
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
import gzip
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

# 压缩前体积保护上限（避免对极端巨型响应耗费 CPU 压缩）与最终写入 Redis 硬上限（96 KB）。
MAX_RAW_CACHE_BYTES = 512 * 1024
MAX_CACHE_BYTES = 96 * 1024
COMPRESS_THRESHOLD_BYTES = 2048
_GZ_PREFIX = "gz:"

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


def _encode_payload(payload: str, *, max_bytes: int, max_raw_bytes: int = MAX_RAW_CACHE_BYTES) -> str | None:
    raw = payload.encode("utf-8")
    if len(raw) > max_raw_bytes:
        return None
    if len(raw) >= COMPRESS_THRESHOLD_BYTES:
        compressed = _GZ_PREFIX + base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")
        candidate = compressed if len(compressed) < len(raw) else payload
    else:
        candidate = payload
    if len(candidate.encode("utf-8")) > max_bytes:
        return None
    return candidate


def _decode_payload(stored: str | bytes) -> str | None:
    text = stored.decode("utf-8") if isinstance(stored, bytes) else stored
    if text.startswith(_GZ_PREFIX):
        try:
            return gzip.decompress(base64.b64decode(text[len(_GZ_PREFIX):])).decode("utf-8")
        except Exception:  # noqa: BLE001 - 损坏数据视为未命中
            return None
    return text


async def _safe_get(redis: Any, key: str) -> str | None:
    if redis is None:
        return None
    try:
        stored = await redis.get(key)
    except Exception:  # noqa: BLE001 - Redis 不可用时降级回源
        return None
    if not stored:
        return None
    return _decode_payload(stored)


async def _safe_set(redis: Any, key: str, payload: str, ttl: int, max_bytes: int) -> None:
    if redis is None or ttl <= 0:
        return
    encoded = _encode_payload(payload, max_bytes=max_bytes)
    if encoded is None:
        logger.debug("skip caching oversized payload key=%s max=%s", key, max_bytes)
        return
    try:
        await redis.set(key, encoded, ex=ttl)
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
    """缓存 JSON 字典响应（使用紧凑 JSON 编码 + 自动 gzip 节省 Redis 内存）；key 为 None 时直接回源。"""
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
