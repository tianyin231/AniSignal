"""低频读取 X 公开账号页；登录、空壳和访问拒绝均计入来源健康。"""

import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from app.collectors.base import CollectorError, USER_AGENT
from app.config import get_settings
from app.models.enums import Platform
from app.parsers.x import normalize_account, parse_page
from app.schemas.content import FetchResult

logger = logging.getLogger(__name__)


class XCollector:
    platform = Platform.X

    async def _read(self, client, url):
        response = await client.get(url)
        response.raise_for_status()
        if urlsplit(str(response.url)).path.startswith(("/i/jf/onboarding", "/i/flow/login", "/login")):
            raise CollectorError("X 公开页面要求登录，无法匿名采集")
        return response.text

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type != "account_dynamics":
            raise CollectorError("X 目前仅支持账号动态监控")
        account = normalize_account(target_value)
        try:
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                         timeout=get_settings().http_timeout_seconds) as client:
                source = await self._read(client, f"https://x.com/{account}")
                items = [item for item in parse_page(source) if item["author"].lower() == account][:limit]
                if not items:
                    raise CollectorError("X 账号页没有可解析的原帖，可能暂不可公开读取或页面格式已变化")
                # 每轮最多补两篇，避免长帖或图片帖把低频账号监控放大成大量请求。
                pending = [item for item in items if item["truncated"] or not item["text"]][:2]
                for item in pending:
                    await asyncio.sleep(3)
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
