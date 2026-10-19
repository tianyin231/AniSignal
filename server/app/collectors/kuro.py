"""鸣潮官网公告采集器：库洛公开 CDN JSON（国服 G152 / 国际服 G153），匿名可访问。

端点实测见 data-sources.md；contentPrefix 可能含多个备用 CDN，此处用主地址，
失败由重试与来源健康机制兜底。
"""

import logging
import re

import httpx

from app.collectors.base import USER_AGENT, CollectorError, extract_items, get_json
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult

logger = logging.getLogger(__name__)

# 公告 ID 的白名单形态：库洛 notice.json 里实测是纯数字；留出字母/下划线/连字符的余量，
# 但不接受 `/` `.` `?` `#` `%` `@` `:` 这些会改变出站 URL 语义的字符。
_NOTICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

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
                # SEC-027：notice_id 原样插进出站 URL 的 path 段。上游 notice.json 一旦
                # 被污染（CDN 被入侵/中间人/配置错误返回了别的内容），`../`、`?`、`#`
                # 就会把请求指到同一 host 的任意路径——httpx 只归一化字面 `..`，不解码
                # `..%2f`，也不阻止 `?`/`#` 截断后续路径段。这里按白名单收敛：非法的
                # 整条跳过（列表信息仍可入库，由版本机制补详情），并留一条 warning。
                if not _NOTICE_ID_RE.fullmatch(notice_id):
                    logger.warning(
                        "kuro notice_id 形态非法，跳过详情抓取：%r", notice_id[:64]
                    )
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
