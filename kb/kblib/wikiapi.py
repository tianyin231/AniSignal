"""MediaWiki API 客户端：限速 + 重试 + 分页。

传输走 curl 子进程——本环境的网络沙箱只放行 curl 出网，Python 原生 socket
会被静默丢弃（表现为读超时）。限速、重试、批次拆分逻辑都在这里，换传输方式
不影响上层。

wiki 地址、礼貌参数（rps/超时/重试/批次）全部来自游戏配置（games/*.yaml），
`MediaWikiClient(game="arknights")` 就打 PRTS wiki，客户端代码不分游戏。

只读文本，不下载图片；429/5xx 指数退避。
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
import time
import urllib.parse
from typing import Any, Iterator

from . import gamecfg

log = logging.getLogger("kb.wikiapi")

CURL = shutil.which("curl") or "curl"
STATUS_SEP = "\n@@HTTP_STATUS@@"


class ApiError(RuntimeError):
    """重试也没用的错误（4xx 等）。"""


class TransientError(RuntimeError):
    """值得重试的错误（超时、连接失败、5xx、429）。"""


class RateLimiter:
    """最小间隔限速器（带抖动，避免和别的请求撞车）。"""

    def __init__(self, rps: float) -> None:
        self.min_interval = 1.0 / max(rps, 0.05)
        self._next = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
            now = time.monotonic()
        self._next = now + self.min_interval * random.uniform(0.9, 1.1)


class MediaWikiClient:
    def __init__(self, rps: float | None = None, max_retries: int | None = None,
                 timeout: int | None = None, batch_titles: int | None = None,
                 game: str | None = None) -> None:
        cfg = gamecfg.load_game(game)
        self.game = cfg
        self.api_url = cfg.api_url
        self.user_agent = cfg.user_agent
        self.limiter = RateLimiter(cfg.rps if rps is None else rps)
        self.max_retries = cfg.max_retries if max_retries is None else max_retries
        self.timeout = cfg.timeout if timeout is None else timeout
        self.batch_titles = cfg.batch_titles if batch_titles is None else batch_titles
        self.request_count = 0

    # ---- 底层 ----
    def _curl(self, url: str) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                [CURL, "-sS", "--compressed", "-m", str(self.timeout),
                 "-A", self.user_agent, "-w", STATUS_SEP + "%{http_code}", "--", url],
                capture_output=True, timeout=self.timeout + 20,
            )
        except subprocess.TimeoutExpired as e:
            raise TransientError(f"curl 超时（{self.timeout}s）") from e
        if proc.returncode != 0:
            raise TransientError(
                f"curl 退出码 {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:200]}"
            )
        out = proc.stdout.decode("utf-8", "replace")
        if STATUS_SEP not in out:
            raise TransientError("curl 输出缺少状态码")
        body, _, code_s = out.rpartition(STATUS_SEP)
        try:
            return int(code_s.strip()), body
        except ValueError as e:
            raise TransientError(f"状态码无法解析：{code_s[:40]!r}") from e

    def _raw(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        url = self.api_url + "?" + urllib.parse.urlencode(params)
        last: Exception | None = None
        for attempt in range(self.max_retries):
            self.limiter.wait()
            try:
                code, body = self._curl(url)
                self.request_count += 1
                if code == 200:
                    return json.loads(body)
                if code in (429, 500, 502, 503, 504) or code >= 500:
                    raise TransientError(f"HTTP {code}")
                raise ApiError(f"HTTP {code}：{body[:200]}")
            except TransientError as e:
                last = e
                delay = min(2 ** attempt, self.game.backoff_cap)
                log.warning("%s，%.1fs 后重试（%d/%d）", e, delay, attempt + 1, self.max_retries)
            except json.JSONDecodeError as e:
                last = e
                log.warning("响应不是 JSON，%.1fs 后重试（%d/%d）",
                            min(2 ** attempt, self.game.backoff_cap), attempt + 1,
                            self.max_retries)
            time.sleep(min(2 ** attempt, self.game.backoff_cap))
        raise ApiError(f"请求失败 {url}: {last!r}")

    def query(self, **params: Any) -> dict[str, Any]:
        data = self._raw({"action": "query", **params})
        if "error" in data:
            raise ApiError(f"API error: {data['error']}")
        return data.get("query", {})

    # ---- 高层 ----
    def iter_all_titles(self, namespace: int = 0, page_size: int = 500,
                        filter_redir: str = "nonredirects") -> Iterator[str]:
        """分页列出命名空间下的条目标题。

        filter_redir: "nonredirects"（默认，正文抓取用）/ "redirects"（别名用）/
        "all"（不过滤）。
        """
        cont: dict[str, Any] = {}
        while True:
            data = self._raw(
                {
                    "action": "query",
                    "list": "allpages",
                    "apnamespace": namespace,
                    "aplimit": page_size,
                    "apfilterredir": filter_redir,
                    **cont,
                }
            )
            for p in data.get("query", {}).get("allpages", []):
                yield p["title"]
            if "continue" not in data:
                return
            cont = data["continue"]

    def resolve_redirects(self, titles: list[str]) -> dict[str, str]:
        """别名 → 最终目标页标题。

        用 API 自带的 redirects 解析（redirects=1，只要 revision id 不拉正文），
        比拿回来正则抠 `#REDIRECT [[x]]` 稳：链式重定向 A→B→C 会一次全解析，
        且不受 `#REDIRECT` 写法变体影响。返回的字典不含"自己指向自己"的条目。
        """
        out: dict[str, str] = {}
        for i in range(0, len(titles), self.batch_titles):
            batch = titles[i : i + self.batch_titles]
            q = self.query(prop="revisions", rvprop="ids",
                           titles="|".join(batch), redirects=1)
            hops = {r["from"]: r["to"] for r in q.get("redirects", [])}
            for alias in batch:
                cur, seen = alias, set()
                while cur in hops and cur not in seen:
                    seen.add(cur)
                    cur = hops[cur]
                if cur != alias:
                    out[alias] = cur
        return out

    def fetch_revisions(self, titles: list[str]) -> list[dict[str, Any]]:
        """批量取正文 wikitext，返回 [{page_id,title,rev_id,wikitext}]。"""
        if len(titles) > self.batch_titles:
            raise ValueError(f"单次最多 {self.batch_titles} 个标题")
        q = self.query(
            prop="revisions",
            rvprop="ids|content",
            rvslots="main",
            titles="|".join(titles),
            redirects=0,
        )
        out: list[dict[str, Any]] = []
        for p in q.get("pages", []):
            if p.get("missing"):
                continue
            revs = p.get("revisions") or []
            if not revs:
                continue
            slot = revs[0].get("slots", {}).get("main", {})
            content = slot.get("content")
            if content is None:
                continue
            out.append(
                {
                    "page_id": p.get("pageid"),
                    "title": p["title"],
                    "rev_id": revs[0].get("revid"),
                    "wikitext": content,
                }
            )
        return out

    def fetch_revisions_safe(self, titles: list[str], depth: int = 0) -> tuple[list[dict], list[str]]:
        """带降级的批量抓取：超长先切块，失败再二分。

        BWIKI 上有些页面（联动公告、剧情整理）单篇就几 MB，混在一批里会把整个
        响应拖到超时；这种批次对半拆，拆到单篇还不行就放弃并报告。

        返回 (records, abandoned_titles)。
        """
        if len(titles) > self.batch_titles:
            out: list[dict] = []
            abandoned: list[str] = []
            for i in range(0, len(titles), self.batch_titles):
                r, a = self.fetch_revisions_safe(titles[i : i + self.batch_titles])
                out.extend(r)
                abandoned.extend(a)
            return out, abandoned
        try:
            return self.fetch_revisions(titles), []
        except ApiError as e:
            if len(titles) <= 1:
                log.error("放弃单篇 %s：%s", titles[0] if titles else "-", e)
                return [], ([titles[0]] if titles else [])
            mid = len(titles) // 2
            log.warning("批次抓取失败（%d 篇），拆成 %d+%d 重试：%s",
                        len(titles), mid, len(titles) - mid, e)
            r1, a1 = self.fetch_revisions_safe(titles[:mid], depth + 1)
            r2, a2 = self.fetch_revisions_safe(titles[mid:], depth + 1)
            return r1 + r2, a1 + a2
