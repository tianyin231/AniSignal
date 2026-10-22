"""低频读取 X 公开账号页；登录、空壳和访问拒绝均计入来源健康。"""

import asyncio
import logging
import random
from datetime import timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from app.clock import to_naive_utc, utcnow
from app.collectors.base import CollectorDeferred, CollectorError, USER_AGENT, platform_slot
from app.config import get_settings
from app.models.enums import Platform
from app.parsers.x import normalize_account, parse_page
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs

logger = logging.getLogger(__name__)
_COOLDOWN_KEY = "anisignal:x:cooldown"
_REQUEST_INTERVAL_RANGE_SECONDS = (30, 90)
_DETAIL_CACHE_MS = 24 * 60 * 60 * 1000


class XCollector:
    platform = Platform.X

    def __init__(self):
        self.redis = None
        self._requested = False

    async def _check_cooldown(self):
        if self.redis is not None:
            ttl = await self.redis.pttl(_COOLDOWN_KEY)
            if ttl and ttl > 0:
                raise CollectorDeferred("X 平台处于访问限制冷却期", retry_at=utcnow() + timedelta(milliseconds=ttl))

    async def _pause(self, retry_after=""):
        if self.redis is None:
            return
        seconds = 3600
        if retry_after.isdigit():
            seconds = max(seconds, int(retry_after))
        elif retry_after:
            try:
                seconds = max(seconds, int((to_naive_utc(parsedate_to_datetime(retry_after)) - utcnow()).total_seconds()))
            except (TypeError, ValueError):
                pass
        ttl = max(seconds * 1000, await self.redis.pttl(_COOLDOWN_KEY) or 0)
        await self.redis.set(_COOLDOWN_KEY, "1", px=ttl)

    async def _read(self, client, url):
        await self._check_cooldown()
        if self.redis is not None:
            # 每次重新抽取间隔；主页和全文共用 Redis 槽位，多 worker 也不会连发。
            async with platform_slot(self.redis, "x", random.uniform(*_REQUEST_INTERVAL_RANGE_SECONDS)):
                await self._check_cooldown()
                response = await client.get(url)
        else:
            if self._requested:
                await asyncio.sleep(random.uniform(*_REQUEST_INTERVAL_RANGE_SECONDS))
            self._requested = True
            response = await client.get(url)
        if response.status_code in {401, 403, 429}:
            await self._pause(response.headers.get("Retry-After", ""))
        response.raise_for_status()
        if urlsplit(str(response.url)).path.startswith(("/i/jf/onboarding", "/i/flow/login", "/login")):
            await self._pause()
            raise CollectorError("X 公开页面要求登录，无法匿名采集")
        return response.text

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "account_dynamics":
            raise CollectorError("X 目前仅支持账号动态监控")
        account = normalize_account(target_value)
        try:
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                         timeout=get_settings().http_timeout_seconds, **http_proxy_kwargs(self)) as client:
                source = await self._read(client, f"https://x.com/{account}")
                items = [item for item in parse_page(source) if item["author"].lower() == account][:limit]
                if not items:
                    raise CollectorError("X 账号页没有可解析的原帖，可能暂不可公开读取或页面格式已变化")
                # 每轮最多补两篇，避免长帖或图片帖把低频账号监控放大成大量请求。
                pending = [item for item in items if item["truncated"] or not item["text"]][:2]
                for item in pending:
                    if self.redis is not None:
                        # 同一长帖一天最多尝试展开一次，不反复请求旧的截断帖。
                        reserved = await self.redis.set(f"anisignal:x:detail:{item['id']}", "1", nx=True, px=_DETAIL_CACHE_MS)
                        if not reserved:
                            continue
                    try:
                        detail = await self._read(client, item["url"])
                        full = parse_page(detail, item["url"])
                        if full and full[0]["text"] and not full[0]["truncated"]:
                            item.update(full[0])
                    except (httpx.HTTPError, CollectorError) as exc:
                        logger.warning("X 原帖 %s 未补全，保留公开片段：%s", item["id"], exc)
                        break
        except httpx.HTTPError as exc:
            raise CollectorError(f"X 账号 @{account} 请求失败：{exc}") from exc
        return FetchResult(platform=self.platform, fetch_kind="x_posts", items=items)
