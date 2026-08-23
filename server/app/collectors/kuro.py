"""鸣潮官网公告采集器：库洛公开 CDN JSON（国服 G152 / 国际服 G153），匿名可访问。

端点实测见 data-sources.md；contentPrefix 可能含多个备用 CDN，此处用主地址，
失败由重试与来源健康机制兜底。
"""

import httpx

from app.collectors.base import USER_AGENT, CollectorError, extract_items, get_json
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult

KURO_CONFIG = {
    Platform.KURO_CN: {
        "base": "https://aki-gm-resources-back.aki-game.com",
        "game": "G152",
        "channel": "76402e5b20be2c39f095a152090afddc",
        "lang": "zh-Hans",
    },
    Platform.KURO_GLOBAL: {
        "base": "https://aki-gm-resources-back.aki-game.net",
        "game": "G153",
        "channel": "6eb2a235b30d05efd77bedb5cf60999e",
        "lang": "en",
    },
}


class KuroCollector:
    def __init__(self, region: Platform) -> None:
        if region not in KURO_CONFIG:
            raise ValueError(f"not a kuro region: {region}")
        self.platform = region
        settings = get_settings()
        self._timeout = settings.http_timeout_seconds
        self._retries = settings.http_max_retries

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "notices":
            raise CollectorError(f"unsupported kuro target_type: {target_type}")
        items = await self.fetch_notices(limit)
        return FetchResult(platform=self.platform, fetch_kind="kuro_notices", items=items)

    async def fetch_notices(self, limit: int) -> list[dict]:
        conf = KURO_CONFIG[self.platform]
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            list_url = (
                f"{conf['base']}/gamenotice/{conf['game']}/{conf['channel']}/notice.json"
            )
            payload = await get_json(
                client, list_url, retries=self._retries, timeout=self._timeout
            )
            # 实测形状：{"game": [公告项, ...]}（2026-08-22 验证）；键变化时退回通用提取
            if isinstance(payload, dict) and isinstance(payload.get("game"), list):
                entries = [e for e in payload["game"] if isinstance(e, dict)]
            else:
                entries = extract_items(payload)
            items: list[dict] = []
            for entry in entries[:limit]:
                notice_id = str(
                    entry.get("noticeId") or entry.get("id") or entry.get("notice_id") or ""
                )
                if not notice_id:
                    continue
                detail_url = (
                    f"{conf['base']}/gamenotice/content/{conf['game']}/"
                    f"{conf['channel']}/{notice_id}/{conf['lang']}.json"
                )
                try:
                    detail = await get_json(
                        client, detail_url, retries=self._retries, timeout=self._timeout
                    )
                except CollectorError:
                    detail = None  # 列表信息仍可入库，详情缺失由版本机制补
                items.append(
                    {
                        "platform": self.platform,
                        "entry": entry,
                        "detail": detail,
                        "list_url": list_url,
                        "detail_url": detail_url,
                    }
                )
            return items
