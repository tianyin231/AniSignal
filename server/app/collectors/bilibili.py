"""B 站采集器：subprocess 调 bili CLI（二进制经多阶段构建已在镜像内）。

命令形态与已知陷阱见 .agents/skills/anisignal/references/data-sources.md：
- 动态必须用独立的 `dynamics <mid>` 入口（`user --dynamics` 返回空）
- 搜索结果统计字段不全：搜索拿 BV 号后必须再请求 `video` 详情
"""

import asyncio
import json
import logging

from app.collectors.base import CollectorError, extract_items
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult

logger = logging.getLogger(__name__)


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
        return items[:limit]


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


__all__ = ["BilibiliCollector"]
