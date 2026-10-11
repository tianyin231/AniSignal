"""采集器公共设施。

CLI/HTTP 返回的外层结构在不同平台差异很大，且可能随版本变化，
因此统一用 `extract_items` 做容错提取：识别常见包裹字段，拿不到时退回原值，
解析失败的风险由"原始数据先落库 + parser 可重跑"兜底。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from app.schemas.content import FetchResult

# 常见的"数据列表"包裹字段，按优先级尝试
_LIST_KEYS = (
    "results",
    "items",
    "list",
    "data",
    "videos",
    "posts",
    "contents",
    "notices",
    "archives",
    "cards",
)


def extract_items(payload: Any) -> list[dict]:
    """从 CLI/HTTP 返回的 JSON 中提取 per-item dict 列表。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    # miyoushe 风格：{retcode: 0, data: {posts: [...]}} —— 先尝试一层 data 内的列表键
    data = payload.get("data")
    for source in (payload, data if isinstance(data, dict) else None):
        if source is None:
            continue
        for key in _LIST_KEYS:
            value = source.get(key)
            if isinstance(value, list) and all(
                item is None or isinstance(item, dict) for item in value
            ):
                return [item for item in value if isinstance(item, dict)]
    return []


def find_first_list(payload: Any) -> list[dict]:
    """更激进的兜底：深度优先找到第一个 dict 列表。"""
    if isinstance(payload, list):
        if all(item is None or isinstance(item, dict) for item in payload):
            return [item for item in payload if isinstance(item, dict)]
        return []
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            if key in payload:
                found = find_first_list(payload[key])
                if found:
                    return found
        for value in payload.values():
            found = find_first_list(value)
            if found:
                return found
    return []


class CollectorError(RuntimeError):
    """采集失败（网络/CLI/风控），用于健康状态更新。"""


class CollectorDeferred(CollectorError):
    """平台冷却期间未发出请求；延后调度，不算一次真实采集失败。"""

    def __init__(self, message: str, *, retry_at: datetime):
        super().__init__(message)
        self.retry_at = retry_at


USER_AGENT = "AniSignal/0.1 (public content monitor)"


async def get_json(
    client: "httpx.AsyncClient",
    url: str,
    params: dict | None = None,
    retries: int = 2,
    timeout: float = 30.0,
):
    """带指数退避的 GET JSON；HTTP 4xx/5xx 与解析失败统一抛 CollectorError。"""
    import asyncio

    import httpx

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            await asyncio.sleep(min(2**attempt, 30))
        try:
            resp = await client.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
    raise CollectorError(f"GET {url} failed after retries: {last_error}")


@asynccontextmanager
async def platform_slot(redis: Any, platform: str, min_interval_seconds: float):
    """平台礼貌间隔：同平台两次采集的最小间隔，用 Redis SET NX PX 实现。

    单 worker 内也保证了顺序；多 worker 时通过 Redis 全局生效。
    拿不到槽位时等待剩余时间再继续，避免任务直接失败。
    """
    import asyncio

    key = f"anisignal:politeness:{platform}"
    delay = 0.0
    while True:
        acquired = await redis.set(key, "1", nx=True, px=int(min_interval_seconds * 1000))
        if acquired:
            break
        ttl = await redis.pttl(key)
        if ttl is None or ttl <= 0:
            continue  # 键恰好过期，重试
        delay = ttl / 1000.0 + 0.05
        await asyncio.sleep(delay)
    try:
        yield
    finally:
        pass  # 键由 PX 自动过期，无需手动释放


__all__ = [
    "CollectorError",
    "CollectorDeferred",
    "FetchResult",
    "extract_items",
    "find_first_list",
    "platform_slot",
]
