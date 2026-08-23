"""B 站采集器：subprocess 调 bili CLI（二进制经多阶段构建已在镜像内）。

命令形态与已知陷阱见 .agents/skills/anisignal/references/data-sources.md：
- 动态必须用独立的 `dynamics <mid>` 入口（`user --dynamics` 返回空）
- 搜索结果统计字段不全：搜索拿 BV 号后必须再请求 `video` 详情
"""

import asyncio
import json
import logging

import httpx

from app.collectors.base import CollectorError, extract_items
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult

logger = logging.getLogger(__name__)

_DYNAMIC_DETAIL_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
_DYNAMIC_DETAIL_PARAMS = {
    "timezone_offset": -480,
    "platform": "web",
    "gaia_source": "main_web",
    "features": "itemOpusStyle",
}
_DRAW_DETAIL_CACHE: dict[str, dict] = {}
_DRAW_DETAIL_CACHE_LIMIT = 256


class BilibiliCollector:
    platform = Platform.BILIBILI

    def __init__(self) -> None:
        settings = get_settings()
        self._bin = settings.bili_bin
        self._timeout = settings.bili_timeout_seconds
        self._max_retries = settings.bili_max_retries

    async def _run(self, *args: str) -> object:
        """执行 bili 命令并解析 stdout JSON，带指数退避重试。"""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2**attempt, 30))
            try:
                return await self._run_once(*args)
            except Exception as exc:  # noqa: BLE001 - 统一按采集失败处理
                last_error = exc
                logger.warning("bili command %s failed (attempt %d): %s", args, attempt + 1, exc)
        raise CollectorError(f"bili command failed after retries: {last_error}")

    async def _run_once(self, *args: str) -> object:
        proc = await asyncio.create_subprocess_exec(
            self._bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            raise CollectorError(f"bili command timed out after {self._timeout}s: {args}") from None
        if proc.returncode != 0:
            raise CollectorError(
                f"bili exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
            )
        return _loads(stdout.decode(errors="replace"))

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        if target_type == "keyword_search":
            items = await self.search_videos(target_value, limit)
            return FetchResult(platform=self.platform, fetch_kind="bilibili_search", items=items)
        if target_type == "account_videos":
            items = await self.user_videos(target_value, limit)
            return FetchResult(platform=self.platform, fetch_kind="bilibili_user_videos", items=items)
        if target_type == "account_dynamics":
            items = await self.user_dynamics(target_value, limit)
            return FetchResult(platform=self.platform, fetch_kind="bilibili_dynamics", items=items)
        raise CollectorError(f"unsupported bilibili target_type: {target_type}")

    async def _run_nonempty(self, *args: str) -> list[dict]:
        """执行命令并提取列表；空结果重试一次（实测匿名链路存在间歇性空返回）。"""
        import asyncio

        payload = await self._run(*args)
        items = extract_items(payload)
        if items:
            return items
        await asyncio.sleep(3)
        payload = await self._run(*args)
        return extract_items(payload)

    async def search_videos(self, keyword: str, limit: int) -> list[dict]:
        results = await self._run_nonempty(
            "search", keyword, "--type", "video", "-n", str(limit), "-o", "json", "--no-cache"
        )
        details: list[dict] = []
        for item in results[:limit]:
            bvid = item.get("bvid")
            if not bvid:
                continue
            try:
                detail = await self._run("video", str(bvid), "-o", "json", "--no-cache")
            except CollectorError:
                logger.warning("video detail fetch failed for %s, keeping search item", bvid)
                details.append(item)
                continue
            # 详情命令可能返回单项 dict 或包裹结构
            if isinstance(detail, dict) and detail.get("bvid"):
                details.append(detail)
            else:
                found = extract_items(detail)
                details.append(found[0] if found else item)
        return details

    async def user_videos(self, mid: str, limit: int) -> list[dict]:
        items = await self._run_nonempty(
            "user", str(mid), "--videos", "-n", str(limit), "-o", "json", "--no-cache"
        )
        return items[:limit]

    async def user_dynamics(self, mid: str, limit: int) -> list[dict]:
        items = await self._run_nonempty(
            "dynamics", str(mid), "-n", str(limit), "-o", "json", "--no-cache"
        )
        return (await self._enrich_draw_dynamics(items))[:limit]

    async def _enrich_draw_dynamics(self, items: list[dict], cap: int = 20) -> list[dict]:
        """匿名补全 DRAW 的正文与图片；结果在 worker 进程内缓存，避免重复请求。"""
        enriched = 0
        attempted = 0
        for item in items:
            if item.get("type") != "DYNAMIC_TYPE_DRAW":
                continue
            if (item.get("text") or "").strip() or item.get("pics"):
                continue
            dynamic_id = str(item.get("id") or "")
            if not dynamic_id:
                continue
            rich = _DRAW_DETAIL_CACHE.get(dynamic_id)
            if rich is None:
                if attempted >= cap:
                    break
                attempted += 1
                try:
                    rich = await self._fetch_draw_detail(dynamic_id)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("anonymous DRAW detail failed for %s: %s", dynamic_id, exc)
                    continue
                if rich:
                    _DRAW_DETAIL_CACHE[dynamic_id] = rich
                    if len(_DRAW_DETAIL_CACHE) > _DRAW_DETAIL_CACHE_LIMIT:
                        _DRAW_DETAIL_CACHE.pop(next(iter(_DRAW_DETAIL_CACHE)))
            if (rich.get("text") or "").strip() or rich.get("pics"):
                item["text"] = rich.get("text") or item.get("text")
                item["pics"] = list(rich.get("pics") or item.get("pics") or [])
                enriched += 1
        if enriched:
            logger.info("enriched %s DRAW dynamics with anonymous public detail", enriched)
        return items

    async def _fetch_draw_detail(self, dynamic_id: str) -> dict:
        """读取无需登录的公开详情；平台拒绝时返回空，绝不切换 Cookie 链路。"""
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://t.bilibili.com/{dynamic_id}",
        }
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            response = await client.get(
                _DYNAMIC_DETAIL_URL,
                params={"id": dynamic_id, **_DYNAMIC_DETAIL_PARAMS},
            )
            response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            logger.warning(
                "anonymous DRAW detail refused for %s: code=%s message=%s",
                dynamic_id,
                payload.get("code"),
                payload.get("message"),
            )
            return {}
        return _extract_dynamic_detail(payload)


def _loads(text: str) -> object:
    """解析 CLI stdout；容忍前后非 JSON 日志行。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [i for i in (text.find("["), text.find("{")) if i >= 0]
        end_candidates = [i for i in (text.rfind("]"), text.rfind("}")) if i >= 0]
        if start_candidates and end_candidates:
            start, end = min(start_candidates), max(end_candidates)
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise CollectorError(f"cannot parse bili output as JSON: {text[:300]!r}")


def _extract_dynamic_detail(payload: dict) -> dict:
    """兼容 B 站旧 DRAW 与新版 OPUS 动态详情结构。"""
    item = ((payload.get("data") or {}).get("item") or {})
    dynamic = ((item.get("modules") or {}).get("module_dynamic") or {})
    desc = dynamic.get("desc") or {}
    major = dynamic.get("major") or {}
    draw = major.get("draw") or {}
    opus = major.get("opus") or {}
    summary = opus.get("summary") or {}
    pics = []
    for image in (opus.get("pics") or draw.get("items") or []):
        src = (image.get("url") or image.get("src")) if isinstance(image, dict) else None
        if isinstance(src, str) and src:
            pics.append("https://" + src[7:] if src.startswith("http://") else src)
    text = desc.get("text") if isinstance(desc, dict) else None
    if not text and isinstance(summary, dict):
        text = summary.get("text")
    return {"text": text or "", "pics": pics}


__all__ = ["BilibiliCollector"]
