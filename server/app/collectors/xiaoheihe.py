"""小黑盒采集器：账号动态内置直连，关键词搜索与热帖走可选独立只读网关或公开 RSS。"""

from __future__ import annotations

import httpx

from app.collectors.base import USER_AGENT, CollectorError, FetchResult, get_json
from app.collectors.rss import RssCollector
from app.config import get_settings
from app.models.enums import Identity, Platform
from app.parsers.xiaoheihe import extract_link_id, parse_account_post, parse_post
from app.schemas.content import NormalizedAuthor, NormalizedContent
from app.services.collector_proxy import fetch_with_fallback, http_proxy_kwargs
from app.services.urls import safe_endpoint

_ACCOUNT_POSTS_URL = "https://api.xiaoheihe.cn/bbs/app/profile/user/link/list_v2"
_ACCOUNT_POSTS_LIMIT = 20


class XiaoheiheCollector:
    """遵循全站统一采集器契约（支持代理池回退、请求计数与标准 FetchResult）。"""

    platform = Platform.XIAOHEIHE

    def __init__(self) -> None:
        self.proxy_url: str | None = None
        self.request_count: int = 0
        self.bridge_url: str = ""
        self.bridge_token: str = ""
        self.feed_url: str = ""

    def configure(self, cfg: dict | None) -> None:
        data = cfg or {}
        self.bridge_url = str(data.get("bridge_url") or "").strip().rstrip("/")
        self.bridge_token = str(data.get("bridge_token") or "").strip()
        self.feed_url = str(data.get("feed_url") or "").strip()

    def _client(self, *, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        merged = {"User-Agent": USER_AGENT, **(headers or {})}
        return httpx.AsyncClient(headers=merged, **http_proxy_kwargs(self))

    async def _public_posts(self, uid: str, limit: int, *, retries: int = 1) -> tuple[NormalizedAuthor, list[dict]]:
        async with self._client() as client:
            self.request_count += 1
            payload = await get_json(
                client,
                _ACCOUNT_POSTS_URL,
                params={"userid": uid, "offset": 0, "limit": limit},
                retries=retries,
                timeout=get_settings().http_timeout_seconds,
            )
        result = payload.get("result") if isinstance(payload, dict) and payload.get("status") == "ok" else None
        user = result.get("user") if isinstance(result, dict) else None
        author_id = str(user.get("userid") or "") if isinstance(user, dict) else ""
        posts = result.get("post_links") if isinstance(result, dict) else None
        if not (author_id.isascii() and author_id.isdecimal()) or not isinstance(posts, list):
            raise CollectorError("小黑盒账号暂时无法读取，请检查 UID 或稍后重试")
        author = NormalizedAuthor(
            external_id=author_id,
            name=str(user.get("username") or "")[:64] or None,
            identity=Identity.USER,
        )
        return author, posts[:limit]

    async def resolve_uid(self, target: str) -> str:
        author, _ = await self._public_posts(target, 1, retries=0)
        return author.external_id

    async def _fetch_account(self, uid: str, limit: int) -> FetchResult:
        cap = max(1, min(int(limit or _ACCOUNT_POSTS_LIMIT), _ACCOUNT_POSTS_LIMIT))
        author, posts = await self._public_posts(uid, cap, retries=1)
        if uid.isdecimal() and author.external_id != uid:
            raise CollectorError("小黑盒返回的作者与监控 UID 不符")
        items: list[dict] = []
        seen: set[str] = set()
        author_payload = author.model_dump(mode="json")
        for raw in posts:
            if not isinstance(raw, dict):
                continue
            parsed = parse_account_post(raw, author)
            if parsed is None or parsed.external_id in seen:
                continue
            seen.add(parsed.external_id)
            enriched = dict(raw)
            enriched["_author"] = author_payload
            if "view" in parsed.metrics:
                enriched.setdefault("view", parsed.metrics["view"])
            items.append(enriched)
        return FetchResult(platform=self.platform, fetch_kind="xiaoheihe_posts", items=items)

    async def _fetch_bridge(self, path: str, params: dict) -> FetchResult:
        base = safe_endpoint(self.bridge_url)
        if not base:
            raise CollectorError("未配置小黑盒采集网关，请先在管理端站点设置中配置")
        headers = {"Authorization": f"Bearer {self.bridge_token}"} if self.bridge_token else {}
        async with self._client(headers=headers) as client:
            self.request_count += 1
            payload = await get_json(
                client,
                f"{base.rstrip('/')}{path}",
                params=params,
                retries=1,
                timeout=get_settings().http_timeout_seconds,
            )
        raw_items = payload.get("items") if isinstance(payload, dict) else (payload if isinstance(payload, list) else None)
        if not isinstance(raw_items, list):
            raise CollectorError("小黑盒采集网关返回格式无效")
        items: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                parsed = parse_post(raw)
            except ValueError:
                continue
            if parsed.external_id in seen:
                continue
            seen.add(parsed.external_id)
            enriched = dict(raw)
            if "view" in parsed.metrics:
                enriched.setdefault("view", parsed.metrics["view"])
            items.append(enriched)
        return FetchResult(platform=self.platform, fetch_kind="xiaoheihe_posts", items=items)

    async def _fetch_rss_hot(self, limit: int) -> FetchResult:
        url = self.feed_url.strip()
        if not url:
            raise CollectorError("未配置小黑盒热帖采集源，请先在管理端站点设置中配置网关或公开 RSS 地址")
        rss = RssCollector()
        rss.proxy_url = self.proxy_url
        rss.source_config = {"engagement": True}
        self.request_count += 1
        result = await rss.fetch("feed", url, limit)
        items: list[dict] = []
        seen: set[str] = set()
        for entry in result.items:
            if not entry.get("title"):
                continue
            link_id = extract_link_id(entry.get("url"))
            if not link_id or link_id in seen:
                continue
            seen.add(link_id)
            items.append({
                "url": f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}",
                "title": str(entry["title"])[:240],
                "text": str(entry.get("text") or "")[:4000] or None,
                "author": str(entry.get("author") or "")[:64] or None,
                "published_at": entry.get("published"),
                "media": list(entry.get("media") or []),
                "like": entry.get("like"),
                "reply": entry.get("reply"),
            })
        if not items:
            raise CollectorError("小黑盒订阅源本轮没有返回可用的公开原帖")
        return FetchResult(platform=self.platform, fetch_kind="xiaoheihe_posts", items=items)

    async def fetch(self, target_type: str, target_value: str, limit: int = 20) -> FetchResult:
        if target_type in {"account_dynamics", "xhh_account"}:
            return await self._fetch_account(target_value, limit)
        if target_type in {"keyword_search", "xhh_keyword"}:
            return await self._fetch_bridge("/v1/search", {"q": target_value, "limit": limit})
        if target_type in {"feed", "hot_posts"}:
            if self.bridge_url:
                return await self._fetch_bridge("/v1/hot", {"topic": target_value or "hot", "limit": limit})
            return await self._fetch_rss_hot(limit)
        raise CollectorError(f"小黑盒不支持采集类型：{target_type}")

    async def test_connection(self) -> tuple[bool, str]:
        if self.bridge_url:
            base = safe_endpoint(self.bridge_url)
            if not base:
                return False, "网关地址格式无效"
            headers = {"Authorization": f"Bearer {self.bridge_token}"} if self.bridge_token else {}
            try:
                async with self._client(headers=headers) as client:
                    resp = await client.get(
                        f"{base.rstrip('/')}/healthz",
                        timeout=min(10.0, get_settings().http_timeout_seconds),
                    )
                    if resp.status_code == 200:
                        return True, "小黑盒采集网关连通正常（/healthz）"
                res = await self._fetch_bridge("/v1/hot", {"topic": "hot", "limit": 1})
                return True, f"小黑盒采集网关连通正常（返回 {len(res.items)} 条样本）"
            except Exception as exc:  # noqa: BLE001
                return False, f"小黑盒采集网关连接失败：{exc}"[:200]
        if self.feed_url:
            try:
                res = await self._fetch_rss_hot(3)
                return True, f"小黑盒公开 RSS 连通正常（解析到 {len(res.items)} 条原帖）"
            except Exception as exc:  # noqa: BLE001
                return False, f"小黑盒公开 RSS 连接失败：{exc}"[:200]
        return False, "未配置采集网关或公开 RSS 地址（账号监控内置直连，无需额外配置）"


async def resolve_account_uid(target: str, session=None) -> str:
    collector = XiaoheiheCollector()
    if session is not None:
        return await fetch_with_fallback(collector, session, Platform.XIAOHEIHE.value, lambda: collector.resolve_uid(target))
    return await collector.resolve_uid(target)


async def collect_account_posts(uid: str, session=None) -> list[NormalizedContent]:
    collector = XiaoheiheCollector()
    if session is not None:
        result = await fetch_with_fallback(
            collector, session, Platform.XIAOHEIHE.value, lambda: collector.fetch("account_dynamics", uid, _ACCOUNT_POSTS_LIMIT)
        )
    else:
        result = await collector.fetch("account_dynamics", uid, _ACCOUNT_POSTS_LIMIT)
    return [parse_post(item) for item in result.items]
