"""NIKKE 国际服官网公开 CMS；与官网网页使用相同的未登录读取接口。"""

import asyncio
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlencode

import httpx

from app.collectors.base import CollectorError, USER_AGENT
from app.config import get_settings
from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, FetchResult, NormalizedAuthor, NormalizedContent
from app.services.collector_proxy import http_proxy_kwargs

API = "https://na-community.playerinfinite.com/api/gpts.information_feeds_svr.InformationFeedsSvr/GetContentByLabel"
# 2026-10-29 官网 GetLabelList / news 脚本核验：NEWS、NOTICE、IMPORTANT，排除弹窗和图库栏目。
COLUMNS = ((309, 496), (309, 892), (308, 494))


class NikkeCollector:
    platform = Platform.GAME_OFFICIAL

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if (target_type, target_value) != ("notices", "nikke_global"):
            raise CollectorError("NIKKE 定制源只支持国际服官方公告")
        settings = get_settings()
        headers = {"User-Agent": USER_AGENT, "X-GameId": "16", "X-AreaId": "na",
                   "X-Source": "pc_web", "X-Language": "en", "Referer": "https://nikke-en.com/"}
        items = {}
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, headers=headers, **http_proxy_kwargs(self)) as client:
            for index, (primary, secondary) in enumerate(COLUMNS):
                if index:
                    await asyncio.sleep(1)
                params = {"gameid": "16", "language": ["en"], "offset": 0, "get_num": limit,
                          "ext_info_type_list": [0, 1, 2], "primary_label_id": primary, "secondary_label_id": secondary}
                for attempt in range(settings.http_max_retries + 1):
                    try:
                        response = await client.post(API, json=params)
                        response.raise_for_status()
                        payload = response.json()
                        break
                    except (httpx.HTTPError, ValueError) as exc:
                        if attempt == settings.http_max_retries:
                            raise CollectorError(f"NIKKE 官网公告读取失败：{type(exc).__name__}") from exc
                        await asyncio.sleep(min(2 ** (attempt + 1), 30))
                if payload.get("code") != 0:
                    raise CollectorError(f"NIKKE 官网返回错误：{payload.get('code')}")
                for item in payload.get("data", {}).get("info_content", []):
                    items.setdefault(str(item["content_id"]), item)
        if not items:
            raise CollectorError("NIKKE 官网未返回公告，请检查官方栏目是否变更")
        ordered = sorted(items.values(), key=lambda item: int(item.get("pub_timestamp") or 0), reverse=True)
        return FetchResult(platform=self.platform, fetch_kind="nikke_notices", items=ordered[:limit])


def parse_notice(raw: dict) -> NormalizedContent:
    cid = str(raw["content_id"])
    return NormalizedContent(
        platform=Platform.GAME_OFFICIAL, content_type=ContentType.NOTICE, external_id=f"nikke_global:{cid}",
        title=unescape(raw["title"]), text=raw.get("content_part") or raw.get("content_desc") or "",
        url="https://nikke-en.com/newsdetail.html?" + urlencode({"content_id": cid, "father_content_id": cid}),
        published_at=datetime.fromtimestamp(int(raw["pub_timestamp"]), timezone.utc),
        author=NormalizedAuthor(external_id="nikke_global", name="NIKKE 国际服官方", identity=Identity.OFFICIAL),
        media=[url for url in raw.get("pic_urls", []) if url.startswith(("https://", "http://"))],
        metrics={"view": int(raw.get("view_num") or 0)}, extra={"language": "en", "region": "global"},
    )
