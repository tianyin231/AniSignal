"""B 站采集器：subprocess 调 bili CLI（二进制经多阶段构建已在镜像内）。

命令形态与已知陷阱见 .agents/skills/anisignal/references/data-sources.md：
- 动态必须用独立的 `dynamics <mid>` 入口（`user --dynamics` 返回空）
- 搜索结果统计字段不全：搜索拿 BV 号后必须再请求 `video` 详情
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

import httpx

from app.clock import utcnow
from app.collectors.base import CollectorDeferred, CollectorError, extract_items
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs, redact_proxy

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
_DRAW_DETAIL_RETRY_AT: dict[str, datetime] = {}
_RICH_DYNAMIC_TYPES = {"DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_ARTICLE"}

# 进程级熔断：同一 bili 命令连续被风控拒绝时暂停一段时间，避免对已门禁端点
# 持续硬打（collector 每个采集任务新建实例，状态必须放模块级才能跨任务生效）。
_CIRCUIT_OPEN_AFTER = 2
_CIRCUIT_COOLDOWN = timedelta(minutes=30)
_CIRCUIT_STATE: dict[str, dict] = {}


class BiliRiskControlError(CollectorError):
    """B 站风控门禁拒绝匿名访问。门禁按 IP 持续生效，秒级重试只会加剧打点。"""


def _circuit_open(command: str) -> bool:
    state = _CIRCUIT_STATE.get(command)
    until = state["until"] if state else None
    return bool(until and until > utcnow())


def _circuit_record_failure(command: str) -> None:
    state = _CIRCUIT_STATE.setdefault(command, {"consecutive": 0, "until": None})
    state["consecutive"] += 1
    if state["consecutive"] >= _CIRCUIT_OPEN_AFTER:
        state["until"] = utcnow() + _CIRCUIT_COOLDOWN
        logger.warning(
            "bili %s circuit opened for %s after %d consecutive risk-control refusals",
            command,
            _CIRCUIT_COOLDOWN,
            state["consecutive"],
        )


def _circuit_reset(command: str) -> None:
    _CIRCUIT_STATE.pop(command, None)


# bili CLI 的选项白名单：_run 只放行这些以 `-` 开头的参数。这是第二层防线——
# 第一层在 _external_arg：外部数据（target_value / 上游 bvid）根本不许以 `-` 开头。
# 只靠这份白名单是不够的：外部值恰好等于 `-o`/`--type` 时会被 CLI 当选项重新解析。
_BILI_FLAGS = frozenset({"--type", "--videos", "--no-cache", "-n", "-o", "--order"})


def _external_arg(value: object, label: str) -> str:
    """校验一个来自外部（DB 里的 target_value、上游 JSON 的 bvid）的 argv 值。

    判据是「位置」而不是「值」：位置参数永远不该长得像选项，所以以 `-` 开头一律拒。
    """
    text = str(value if value is not None else "")
    if not text.strip():
        raise CollectorError(f"{label} 为空，拒绝执行 bili")
    if text.startswith("-"):
        raise CollectorError(f"{label} 以 - 开头（会被 CLI 当作选项解析），已拒绝执行：{text!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise CollectorError(f"{label} 含控制字符，已拒绝执行：{text!r}")
    return text


def _guard_argv(args: tuple[str, ...]) -> None:
    """拒绝把外部数据当成 CLI 选项传入（第一个参数是命令名，其余按白名单判）。"""
    for arg in args[1:]:
        if arg.startswith("-") and arg not in _BILI_FLAGS:
            raise CollectorError(f"bili 参数含未授权选项 {arg!r}，已拒绝执行")


class BilibiliCollector:
    platform = Platform.BILIBILI

    def __init__(self) -> None:
        settings = get_settings()
        self._bin = settings.bili_bin
        self._timeout = settings.bili_timeout_seconds
        self._max_retries = settings.bili_max_retries
        self.newest_first = False

    async def _run(self, *args: str) -> object:
        """执行 bili 命令并解析 stdout JSON，带指数退避重试。

        风控门禁拒绝不重试：门禁按 IP 持续生效，秒级重试无意义；
        连续两次拒绝即熔断该命令一段时间，冷却到期后由下一次调用自然探测。
        """
        command = args[0] if args else ""
        _guard_argv(args)
        if _circuit_open(command):
            raise CollectorDeferred(
                f"bili {command} skipped: circuit open after risk-control refusals",
                retry_at=_CIRCUIT_STATE[command]["until"],
            )
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2**attempt, 30))
            try:
                payload = await self._run_once(*args)
            except BiliRiskControlError:
                _circuit_record_failure(command)
                raise
            except Exception as exc:  # noqa: BLE001 - 统一按采集失败处理
                last_error = exc
                logger.warning("bili command %s failed (attempt %d): %s", args, attempt + 1, exc)
            else:
                _circuit_reset(command)
                return payload
        raise CollectorError(f"bili command failed after retries: {last_error}")

    async def _run_once(self, *args: str) -> object:
        env = os.environ.copy()
        # 通过子进程环境传递，代理密码不出现在 argv / 命令日志中。
        for name in ("BILI_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
        if getattr(self, "proxy_url", None):
            env["BILI_PROXY"] = self.proxy_url
        proc = await asyncio.create_subprocess_exec(
            self._bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            raise CollectorError(f"bili command timed out after {self._timeout}s: {args}") from None
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode != 0:
            stderr_text = redact_proxy(stderr.decode(errors="replace"), getattr(self, "proxy_url", None))
            # bili 0.3: 4=匿名首屏静默拒绝，6=限频；不再只靠英文 risk control。
            if proc.returncode in (4, 6) or "risk control" in stderr_text.casefold():
                raise BiliRiskControlError(
                    f"bili exited {proc.returncode}: {stderr_text[:500]}"
                ) from None
            raise CollectorError(f"bili exited {proc.returncode}: {stderr_text[:500]}")
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
        keyword = _external_arg(keyword, "搜索词")
        results = await self._run_nonempty(
            "search", keyword, "--type", "video", "-n", str(limit), "-o", "json", "--no-cache",
            *(["--order", "pubdate"] if self.newest_first else []),
        )
        if self.newest_first:
            # 快讯首屏已含发布时间和简介；不逐条请求易受限的详情接口。
            return results[:limit]
        details: list[dict] = []
        for item in results[:limit]:
            bvid = item.get("bvid")
            if not bvid:
                continue
            if _circuit_open("video"):
                # 熔断期间不触碰详情端点；搜索结果自带 view/reply 等核心指标，直接兜底
                details.append(item)
                continue
            try:
                # bvid 来自上游 JSON：形状不对就退回列表自带字段，不为一条脏数据丢命
                safe_bvid = _external_arg(bvid, "bvid")
                detail = await self._run("video", safe_bvid, "-o", "json", "--no-cache")
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
        mid = _external_arg(mid, "账号 MID")
        items = await self._run_nonempty(
            "user", str(mid), "--videos", "-n", str(limit), "-o", "json", "--no-cache"
        )
        return items[:limit]

    async def user_dynamics(self, mid: str, limit: int) -> list[dict]:
        mid = _external_arg(mid, "账号 MID")
        items = await self._run_nonempty(
            "dynamics", str(mid), "-n", str(limit), "-o", "json", "--no-cache"
        )
        return (await self._enrich_draw_dynamics(items))[:limit]

    async def _enrich_draw_dynamics(self, items: list[dict], cap: int = 20) -> list[dict]:
        """补全 CLI 未展开的 DRAW/ARTICLE（OPUS）；共用详情缓存与冷却。"""
        enriched = 0
        attempted = 0
        for item in items:
            if item.get("type") not in _RICH_DYNAMIC_TYPES:
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
            if (rich.get("text") or "").strip() or rich.get("pics"):
                item["text"] = rich.get("text") or item.get("text")
                item["pics"] = list(rich.get("pics") or item.get("pics") or [])
                enriched += 1
        if enriched:
            logger.info("enriched %s DRAW dynamics with anonymous public detail", enriched)
        return items

    async def _fetch_draw_detail(self, dynamic_id: str) -> dict:
        """列表补全与历史回填共用缓存；风控冷却，失效动态延后重查。"""
        if dynamic_id in _DRAW_DETAIL_CACHE:
            return _DRAW_DETAIL_CACHE[dynamic_id]
        retry_at = _DRAW_DETAIL_RETRY_AT.get(dynamic_id)
        if (retry_at and retry_at > utcnow()) or _circuit_open("dynamic-detail"):
            return {}
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://t.bilibili.com/{dynamic_id}",
        }
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers, **http_proxy_kwargs(self)) as client:
            response = await client.get(
                _DYNAMIC_DETAIL_URL,
                params={"id": dynamic_id, **_DYNAMIC_DETAIL_PARAMS},
            )
            if response.status_code in (412, 429):
                _circuit_record_failure("dynamic-detail")
                _defer_dynamic_detail(dynamic_id)
                logger.warning("anonymous dynamic detail refused: HTTP %s", response.status_code)
                return {}
            response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            if payload.get("code") in (-352, -412, -509):
                _circuit_record_failure("dynamic-detail")
            _defer_dynamic_detail(
                dynamic_id,
                timedelta(hours=6) if payload.get("code") == 4101152 else _CIRCUIT_COOLDOWN,
            )
            logger.warning(
                "anonymous DRAW detail refused for %s: code=%s message=%s",
                dynamic_id,
                payload.get("code"),
                payload.get("message"),
            )
            return {}
        _circuit_reset("dynamic-detail")
        rich = _extract_dynamic_detail(payload)
        if (rich.get("text") or "").strip() or rich.get("pics"):
            _DRAW_DETAIL_RETRY_AT.pop(dynamic_id, None)
            _DRAW_DETAIL_CACHE[dynamic_id] = rich
            if len(_DRAW_DETAIL_CACHE) > _DRAW_DETAIL_CACHE_LIMIT:
                _DRAW_DETAIL_CACHE.pop(next(iter(_DRAW_DETAIL_CACHE)))
        else:
            _defer_dynamic_detail(dynamic_id)
        return rich


def _defer_dynamic_detail(dynamic_id: str, delay: timedelta = _CIRCUIT_COOLDOWN) -> None:
    _DRAW_DETAIL_RETRY_AT[dynamic_id] = utcnow() + delay
    if len(_DRAW_DETAIL_RETRY_AT) > _DRAW_DETAIL_CACHE_LIMIT:
        _DRAW_DETAIL_RETRY_AT.pop(next(iter(_DRAW_DETAIL_RETRY_AT)))


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
