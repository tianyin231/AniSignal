"""游戏官网的公开公告接口；固定提供方，不接受任意 URL。"""

import asyncio
from datetime import timedelta

import httpx

from app.clock import utcnow
from app.collectors.base import CollectorError, USER_AGENT, get_json
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs

GENSHIN_URL = "https://act-api-takumi-static.mihoyo.com/content_v2_user/app/16471662a82d418a/getContentList"
ENDFIELD_URL = "https://web-news.hypergryph.com/api/bulletin"
_DETAIL_CACHE: dict[str, tuple] = {}


class GameOfficialCollector:
    platform = Platform.GAME_OFFICIAL

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "notices" or target_value not in {"genshin", "endfield"}:
            raise CollectorError("游戏官网公告仅支持 genshin / endfield notices")
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers={"User-Agent": USER_AGENT}, **http_proxy_kwargs(self)) as client:
            async def request(url, params):
                return await get_json(client, url, params=params, retries=settings.http_max_retries,
                                      timeout=settings.http_timeout_seconds)

            if target_value == "genshin":
                # 官网脚本的最新频道 719，dtStartTime 是公开发布时间，而非草稿创建时间。
                payload = await request(GENSHIN_URL, {"iAppId": 43, "iChanId": 719, "iPageSize": limit, "iPage": 1, "sLangKey": "zh-cn"})
                if payload.get("retcode") != 0:
                    raise CollectorError("原神官网公告返回错误")
                items = [{"game": "genshin", "article": row} for row in (payload.get("data") or {}).get("list", [])[:limit]]
            else:
                params = {"lang": "zh-cn", "code": "endfield_web"}
                payload = await request(ENDFIELD_URL, {**params, "page": 1, "pageSize": limit})
                if payload.get("code") != 0:
                    raise CollectorError("终末地官网公告返回错误")
                items = []
                for row in (payload.get("data") or {}).get("list", [])[:limit]:
                    cid = str(row["cid"])
                    cached = _DETAIL_CACHE.get(cid)
                    if cached and cached[0] > utcnow() - timedelta(hours=1):
                        article = cached[1]
                    else:
                        # 列表不含完整正文；详情低频读取并缓存，避免每轮重复访问旧公告。
                        await asyncio.sleep(1)
                        detail = await request(f"{ENDFIELD_URL}/{cid}", params)
                        if detail.get("code") != 0:
                            raise CollectorError(f"终末地公告 {cid} 详情返回错误")
                        article = detail["data"]
                        _DETAIL_CACHE[cid] = (utcnow(), article)
                        if len(_DETAIL_CACHE) > 128:
                            _DETAIL_CACHE.pop(next(iter(_DETAIL_CACHE)))
                    items.append({"game": "endfield", "entry": row, "article": article})
        return FetchResult(platform=self.platform, fetch_kind="game_official_notices", items=items)
