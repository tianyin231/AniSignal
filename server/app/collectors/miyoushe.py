"""米游社采集器：公开 searchPosts -> getPostFull 链路（匿名，retcode=0 实测可用）。

注意：公开接口不等于永久稳定 API，保留低频、缓存与重试（data-sources.md）。
"""

import httpx

from app.collectors.base import USER_AGENT, CollectorError, extract_items, get_json
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs

SEARCH_URL = "https://bbs-api.miyoushe.com/post/wapi/searchPosts"
DETAIL_URL = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"


class MiyousheCollector:
    platform = Platform.MIYOUSHE

    def __init__(self) -> None:
        settings = get_settings()
        self._timeout = settings.http_timeout_seconds
        self._retries = settings.http_max_retries
        self._gids = settings.miyoushe_default_gids

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "keyword_search":
            raise CollectorError(f"unsupported miyoushe target_type: {target_type}")
        items = await self.search_posts(target_value, limit)
        return FetchResult(platform=self.platform, fetch_kind="miyoushe_posts", items=items)

    async def search_posts(self, keyword: str, limit: int) -> list[dict]:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, **http_proxy_kwargs(self)) as client:
            payload = await get_json(
                client,
                SEARCH_URL,
                params={
                    "keyword": keyword,
                    "gids": self._gids,
                    "page": 1,
                    "page_size": max(limit, 20),
                },
                retries=self._retries,
                timeout=self._timeout,
            )
            entries = extract_items(payload)
            items: list[dict] = []
            for entry in entries[:limit]:
                post = entry.get("post") if isinstance(entry.get("post"), dict) else entry
                post_id = str(post.get("post_id") or "")
                if not post_id:
                    continue
                try:
                    detail_payload = await get_json(
                        client,
                        DETAIL_URL,
                        params={"post_id": post_id},
                        retries=self._retries,
                        timeout=self._timeout,
                    )
                    detail = detail_payload.get("data") if isinstance(detail_payload, dict) else None
                except CollectorError:
                    detail = None
                items.append({"entry": entry, "detail": detail})
            return items
