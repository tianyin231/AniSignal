"""Steam 公开匿名采集器：官方社区公告 + 实时在线人数与全区/简中好评率快照。

刻意不使用 steamcommunity.com（匿名面登录墙/429）与 steamspy.com（云机房 Cloudflare 403），
全部信号均来自 Steam 官方公开无鉴权接口。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from app.clock import utcnow
from app.collectors.base import USER_AGENT, CollectorError, FetchResult, get_json
from app.config import get_settings
from app.models.enums import Platform
from app.parsers.steam import extract_app_id
from app.services.collector_proxy import http_proxy_kwargs

logger = logging.getLogger(__name__)

STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
STEAM_ONLINE_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_REVIEWS_URL = "https://store.steampowered.com/ajaxappreviews/{appid}"

_DETAIL_CACHE_TTL = timedelta(hours=24)
_DETAIL_CACHE: dict[int, tuple[datetime, dict]] = {}


def _to_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 2_147_483_647 else None


class SteamCollector:
    """采集 Steam 官方公告，并在同一轮顺带采样游戏实时在线与好评统计快照。"""

    platform = Platform.STEAM

    def __init__(self) -> None:
        self.proxy_url: str | None = None
        self.request_count: int = 0
        self.last_snapshot: dict | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=get_settings().http_timeout_seconds,
            follow_redirects=True,
            **http_proxy_kwargs(self),
        )

    async def _optional_json(self, client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
        self.request_count += 1
        try:
            payload = await get_json(
                client,
                url,
                params=params,
                retries=0,
                timeout=min(15.0, get_settings().http_timeout_seconds),
            )
            return payload if isinstance(payload, dict) else None
        except (CollectorError, httpx.HTTPError, ValueError) as exc:
            logger.debug("steam optional endpoint %s failed: %s", url, exc)
            return None

    async def _get_app_detail(self, client: httpx.AsyncClient, app_id: int) -> dict:
        now = utcnow()
        cached = _DETAIL_CACHE.get(app_id)
        if cached and cached[0] > now - _DETAIL_CACHE_TTL:
            return cached[1]
        payload = await self._optional_json(
            client,
            STEAM_APPDETAILS_URL,
            {"appids": app_id, "l": "schinese", "cc": "cn"},
        )
        entry = (payload or {}).get(str(app_id)) if isinstance(payload, dict) else None
        data = entry.get("data") if isinstance(entry, dict) and entry.get("success") else None
        if not isinstance(data, dict):
            return cached[1] if cached else {}
        price_overview = data.get("price_overview") if isinstance(data.get("price_overview"), dict) else {}
        if data.get("is_free"):
            price_formatted = "免费开玩"
            discount_percent = 0
        else:
            price_formatted = str(price_overview.get("final_formatted") or "").strip()[:64] or None
            discount_percent = _to_int(price_overview.get("discount_percent")) or 0
        info = {
            "name": str(data.get("name") or "").strip()[:200] or None,
            "price_formatted": price_formatted,
            "discount_percent": discount_percent,
        }
        _DETAIL_CACHE[app_id] = (now, info)
        if len(_DETAIL_CACHE) > 256:
            _DETAIL_CACHE.pop(next(iter(_DETAIL_CACHE)))
        return info

    async def _collect_snapshot(self, client: httpx.AsyncClient, app_id: int, detail: dict) -> dict | None:
        online_payload = await self._optional_json(client, STEAM_ONLINE_URL, {"appid": app_id})
        player_count = _to_int(((online_payload or {}).get("response") or {}).get("player_count"))

        # appreviews 端点要求 appid 同时出现在路径与 appids 参数中（否则 302 跳转商店首页）。
        reviews_url = STEAM_REVIEWS_URL.format(appid=app_id)
        all_payload = await self._optional_json(
            client,
            reviews_url,
            {"json": 1, "appids": app_id, "language": "all", "purchase_type": "all", "num_per_page": 0},
        )
        all_summary = (all_payload or {}).get("query_summary") if isinstance(all_payload, dict) else None
        all_summary = all_summary if isinstance(all_summary, dict) else {}

        cn_payload = await self._optional_json(
            client,
            reviews_url,
            {"json": 1, "appids": app_id, "language": "schinese", "purchase_type": "all", "num_per_page": 0},
        )
        cn_summary = (cn_payload or {}).get("query_summary") if isinstance(cn_payload, dict) else None
        cn_summary = cn_summary if isinstance(cn_summary, dict) else {}

        snapshot = {
            "app_id": app_id,
            "name": detail.get("name"),
            "player_count": player_count,
            "global_positive": _to_int(all_summary.get("total_positive")),
            "global_negative": _to_int(all_summary.get("total_negative")),
            "global_score_desc": str(all_summary.get("review_score_desc") or "").strip()[:48] or None,
            "cn_positive": _to_int(cn_summary.get("total_positive")),
            "cn_negative": _to_int(cn_summary.get("total_negative")),
            "price_formatted": detail.get("price_formatted"),
            "discount_percent": int(detail.get("discount_percent") or 0),
        }
        has_signal = any(
            snapshot[k] is not None
            for k in ("player_count", "global_positive", "global_negative", "cn_positive", "cn_negative")
        )
        return snapshot if has_signal else None

    async def fetch(self, target_type: str, target_value: str, limit: int = 20) -> FetchResult:
        if target_type != "notices":
            raise CollectorError(f"Steam 目前仅支持官方公告（notices），不支持：{target_type}")
        try:
            app_id = int(extract_app_id(target_value))
        except ValueError as exc:
            raise CollectorError(str(exc)) from None

        self.last_snapshot = None
        cap = max(1, min(int(limit or 20), 30))
        settings = get_settings()
        async with self._client() as client:
            self.request_count += 1
            news_payload = await get_json(
                client,
                STEAM_NEWS_URL,
                params={
                    "appid": app_id,
                    "count": cap,
                    "maxlength": 0,
                    "feeds": "steam_community_announcements",
                },
                retries=settings.http_max_retries,
                timeout=settings.http_timeout_seconds,
            )
            appnews = news_payload.get("appnews") if isinstance(news_payload, dict) else None
            newsitems = appnews.get("newsitems") if isinstance(appnews, dict) else None
            if not isinstance(newsitems, list):
                raise CollectorError(f"Steam 官方公告返回格式无效（AppID {app_id}）")

            detail = await self._get_app_detail(client, app_id)
            self.last_snapshot = await self._collect_snapshot(client, app_id, detail)

        app_name = detail.get("name")
        items = [
            {"app_id": app_id, "app_name": app_name, "item": row}
            for row in newsitems[:cap]
            if isinstance(row, dict)
        ]
        return FetchResult(platform=self.platform, fetch_kind="steam_notices", items=items)
