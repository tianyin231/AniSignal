"""开发者平台公开信源采集器：GitHub 与 GitCode（默认匿名可用，支持 ETag 省配额与限流降级）。

设计边界（见 docs/plans/2026-11-25-general-sources-and-reports.md）：
- 严格仅访问公开只读接口与原生 Atom 源，不处理私有仓库或登录态；
- GitHub Releases 优先走 REST API（带 If-None-Match 304 零配额消耗），遇到 403/429 匿名限流
  或上游故障时自动回退至无配额限制的 https://github.com/{owner}/{repo}/releases.atom；
- GitHub Issues 走公开 REST API（支持单标签上游筛选、多标签本地 OR 筛选，自动剔除 PR），
  匿名触顶时解析 X-RateLimit-Reset 抛出 CollectorDeferred 延后调度而非累加失败；
- GitHub Discussions 直接走公开 Atom（https://github.com/{owner}/{repo}/discussions.atom），
  免 GraphQL Token 匿名可用；
- GitCode Releases / Issues 走公开只读 OpenAPI v5（https://api.gitcode.com/api/v5/repos/...），
  同样支持匿名采集与可选环境变量令牌（ANISIGNAL_GITHUB_TOKEN / ANISIGNAL_GITCODE_TOKEN）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import html2text
import httpx
from parsel import Selector

from app.clock import utcnow
from app.collectors.base import CollectorDeferred, CollectorError, USER_AGENT
from app.collectors.rss import child as xml_child, feed_time, plain as xml_plain, tag as xml_tag, text as xml_text
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.services.collector_proxy import http_proxy_kwargs

logger = logging.getLogger(__name__)

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_WEB_ROOT = "https://github.com"
GITCODE_API_ROOT = "https://api.gitcode.com/api/v5"
GITCODE_WEB_ROOT = "https://gitcode.com"

GITHUB_API_VERSION = "2022-11-28"
MAX_ATOM_BYTES = 5 * 1024 * 1024
MAX_LABELS = 10
MAX_LABEL_LEN = 64

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$|^[A-Za-z0-9]$")
_PRERELEASE_HINT_RE = re.compile(r"(?:^|[.\-_+])(?:alpha|beta|rc|pre|preview|dev|nightly|canary|snapshot)\b", re.I)
_RELEASE_TAG_PATH_RE = re.compile(r"/releases/tag/([^/?#]+)")
_DISCUSSION_NUM_PATH_RE = re.compile(r"/discussions/(\d+)")
_TRENDING_LANG_RE = re.compile(r"^[a-z0-9][a-z0-9.+#_-]{0,31}$")
_INT_RE = re.compile(r"[\d,]+")
TRENDING_PERIODS = ("daily", "weekly", "monthly")

_PLATFORM_HOSTS: dict[str, set[str]] = {
    "github": {"github.com", "www.github.com"},
    "gitcode": {"gitcode.com", "www.gitcode.com"},
}

_PLATFORM_LABELS: dict[str, str] = {
    "github": "GitHub",
    "gitcode": "GitCode",
}


def _fail_fast(message: str) -> CollectorError:
    err = CollectorError(message)
    err.stop_fallback = True  # type: ignore[attr-defined]
    return err


def normalize_repo_target(platform: str, raw: str) -> str:
    """将用户输入规整为 `owner/repo`；支持裸路径与对应平台的公开仓库链接。"""
    plat = str(platform or "").strip().lower()
    if plat not in _PLATFORM_HOSTS:
        raise ValueError(f"不支持的开发者平台：{platform}")
    label = _PLATFORM_LABELS[plat]
    value = str(raw or "").strip()
    if not value:
        raise ValueError(f"请填写 {label} 仓库（如 owner/repo）")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value) or any(ch.isspace() for ch in value):
        raise ValueError(f"{label} 仓库标识不能包含空白或控制字符")

    path = value
    lower = value.lower()
    if "://" in value or lower.startswith(("github.com/", "www.github.com/", "gitcode.com/", "www.gitcode.com/")):
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError(f"请填写有效的公开 {label} 仓库地址或 owner/repo")
        if host not in _PLATFORM_HOSTS[plat]:
            raise ValueError(f"该链接不属于 {label}，请填写 {label} 仓库地址或 owner/repo")
        path = parsed.path

    parts = [seg for seg in path.strip("/").split("/") if seg]
    if len(parts) < 2:
        raise ValueError(f"请填写完整的 {label} 仓库（如 owner/repo 或仓库链接）")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if ".." in owner or ".." in repo or not _SEGMENT_RE.fullmatch(owner) or not _SEGMENT_RE.fullmatch(repo):
        raise ValueError(f"{label} 仓库格式无效，请使用 owner/repo 形式")
    full = f"{owner}/{repo}"
    if len(full) > 128:
        raise ValueError(f"{label} 仓库路径过长（最多 128 字符）")
    return full


def normalize_repo_config(raw: dict | None, *, target_type: str = "releases") -> dict:
    """规范化仓库采集配置：releases 支持 include_prerelease，issues 支持 labels。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    ttype = str(target_type or "").strip().lower()
    if ttype in {"releases", "gh_releases", "gc_releases"}:
        if "include_prerelease" in raw and raw["include_prerelease"] is not None:
            include_pre = bool(raw["include_prerelease"])
            if not include_pre:
                out["include_prerelease"] = False
    if ttype in {"issues", "gh_issues", "gc_issues"}:
        raw_labels = raw.get("labels")
        if isinstance(raw_labels, str):
            raw_labels = [part.strip() for part in re.split(r"[,，\n]+", raw_labels) if part.strip()]
        if isinstance(raw_labels, list):
            cleaned: list[str] = []
            seen: set[str] = set()
            for item in raw_labels:
                if not isinstance(item, str):
                    raise ValueError("标签必须为字符串")
                name = item.strip()
                if not name:
                    continue
                if len(name) > MAX_LABEL_LEN or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
                    raise ValueError(f"单个标签需为 1 至 {MAX_LABEL_LEN} 字且不含控制字符")
                key = name.casefold()
                if key not in seen:
                    seen.add(key)
                    cleaned.append(name)
            if len(cleaned) > MAX_LABELS:
                raise ValueError(f"限定标签最多 {MAX_LABELS} 个")
            if cleaned:
                out["labels"] = cleaned
    return out


def normalize_trending_target(raw: str) -> str:
    """将趋势榜单输入规整为 `daily|weekly|monthly`（可选 `:language`），支持完整 github.com/trending 链接。"""
    value = str(raw or "").strip()
    if not value:
        raise ValueError("请填写榜单周期：daily / weekly / monthly（可选语言如 daily:python）")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value) or any(ch.isspace() for ch in value):
        raise ValueError("榜单周期不能包含空白或控制字符")

    period = ""
    language = ""
    lower = value.lower()
    if "://" in value or lower.startswith(("github.com/", "www.github.com/")):
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in _PLATFORM_HOSTS["github"]:
            raise ValueError("请填写 GitHub Trending 地址或 daily / weekly / monthly")
        segs = [s for s in parsed.path.strip("/").split("/") if s]
        if not segs or segs[0].lower() != "trending":
            raise ValueError("请填写 GitHub Trending 地址或 daily / weekly / monthly")
        if len(segs) >= 2 and segs[1].lower() not in {"developers", "repositories"}:
            language = unquote(segs[1]).strip().lower()
        qs = parse_qs(parsed.query)
        period = (qs.get("since") or ["daily"])[0].strip().lower()
    else:
        head, sep, tail = lower.partition(":")
        period = head.strip()
        language = tail.strip() if sep else ""

    if period not in TRENDING_PERIODS:
        raise ValueError("趋势榜单周期仅支持 daily / weekly / monthly")
    if language and not _TRENDING_LANG_RE.fullmatch(language):
        raise ValueError("编程语言筛选格式无效（如 python、typescript、rust）")
    return f"{period}:{language}" if language else period


def parse_trending_target(value: str) -> tuple[str, str]:
    norm = normalize_trending_target(value)
    period, _, lang = norm.partition(":")
    return period, lang


def _parse_int_text(raw: str | None) -> int:
    if not raw:
        return 0
    m = _INT_RE.search(raw)
    if not m:
        return 0
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return 0


def parse_github_trending_html(
    html: str, *, period: str, language: str = "", limit: int = 25
) -> tuple[list[dict], list[dict]]:
    """解析 github.com/trending SSR HTML，返回 (raw_items, edition_entries)。"""
    sel = Selector(text=html)
    articles = sel.css("article.Box-row")
    if not articles:
        return [], []

    now_iso = utcnow().replace(tzinfo=timezone.utc).isoformat()
    raw_items: list[dict] = []
    entries: list[dict] = []
    seen: set[str] = set()

    for article in articles:
        href = (article.css("h2 a::attr(href)").get() or "").strip()
        if not href:
            continue
        try:
            repo = normalize_repo_target("github", href.lstrip("/"))
        except ValueError:
            continue
        rkey = repo.lower()
        if rkey in seen:
            continue
        seen.add(rkey)

        rank = len(entries) + 1
        desc = " ".join(part.strip() for part in article.css("p::text").getall() if part.strip())
        lang = (article.css('[itemprop="programmingLanguage"]::text').get() or "").strip()

        stars_text = " ".join(article.css('a[href$="/stargazers"]::text').getall())
        forks_text = " ".join(article.css('a[href$="/forks"]::text').getall())
        stars = _parse_int_text(stars_text)
        forks = _parse_int_text(forks_text)
        if not stars and not forks:
            muted_nums = [_parse_int_text(t) for t in article.css("a.Link--muted::text").getall() if t.strip()]
            muted_nums = [n for n in muted_nums if n > 0]
            if muted_nums:
                stars = muted_nums[0]
            if len(muted_nums) > 1:
                forks = muted_nums[1]

        period_stars_text = " ".join(article.css("span.float-sm-right::text").getall())
        period_stars = _parse_int_text(period_stars_text)
        url = f"{GITHUB_WEB_ROOT}/{repo}"

        entries.append(
            {
                "rank": rank,
                "repo": repo,
                "url": url,
                "description": desc,
                "language": lang,
                "stars": stars,
                "forks": forks,
                "period_stars": period_stars,
            }
        )
        raw_items.append(
            {
                "id": git_item_id(repo, "trending", "repo"),
                "platform": "github",
                "kind": "trending",
                "repo": repo,
                "title": repo,
                "body": desc,
                "url": url,
                "published": now_iso,
                "author": repo.split("/")[0],
                "language": lang,
                "stars": stars,
                "forks": forks,
                "period_stars": period_stars,
                "period": period,
                "filter_language": language,
                "rank": rank,
                "source_channel": "trending_html",
            }
        )
        if len(entries) >= limit:
            break
    return raw_items, entries


def repo_web_url(platform: str, repo: str, kind: str = "releases") -> str:
    root = GITHUB_WEB_ROOT if platform == "github" else GITCODE_WEB_ROOT
    norm_kind = {
        "releases": "releases",
        "gh_releases": "releases",
        "gc_releases": "releases",
        "issues": "issues",
        "gh_issues": "issues",
        "gc_issues": "issues",
        "discussions": "discussions",
        "gh_discussions": "discussions",
    }.get(kind, "")
    return f"{root}/{repo}/{norm_kind}" if norm_kind else f"{root}/{repo}"


def git_item_id(repo: str, kind: str, ident: str) -> str:
    """生成跨 API / Atom 回退稳定的 external_id（长度始终 ≤ 64，兼容 RawContent 与 CustomFeedItem）。"""
    clean_ident = str(ident or "").strip()
    base = f"{repo.lower()}:{kind}:{clean_ident}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:48]
    return f"{kind}:{digest}"[:64]


def _atom_html_to_text(html_body: str) -> str:
    raw = (html_body or "").strip()
    if not raw:
        return ""
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.unicode_snob = True
    converter.protect_links = True
    text = converter.handle(raw)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or xml_plain(raw)


def _extract_label_names(raw_labels: Any) -> list[str]:
    if not isinstance(raw_labels, list):
        return []
    out: list[str] = []
    for entry in raw_labels:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
        elif isinstance(entry, str):
            name = entry.strip()
        else:
            name = ""
        if name:
            out.append(name)
    return out


def _matches_labels(item_labels: list[str], wanted: list[str]) -> bool:
    if not wanted:
        return True
    have = {label.casefold() for label in item_labels}
    return any(label.casefold() in have for label in wanted)


def _retry_at_from_headers(headers: httpx.Headers, *, default_minutes: int = 15) -> datetime:
    now = datetime.now(timezone.utc)
    retry_after = headers.get("retry-after")
    if retry_after and retry_after.strip().isdigit():
        seconds = max(60, min(int(retry_after.strip()), 3600))
        return (now + timedelta(seconds=seconds)).replace(tzinfo=None)
    reset_raw = headers.get("x-ratelimit-reset")
    if reset_raw and reset_raw.strip().isdigit():
        try:
            target = datetime.fromtimestamp(int(reset_raw.strip()), tz=timezone.utc)
            delta = (target - now).total_seconds()
            if 30 <= delta <= 3700:
                return (target + timedelta(seconds=15)).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            pass
    return (now + timedelta(minutes=default_minutes)).replace(tzinfo=None)


def _is_github_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    if resp.headers.get("x-ratelimit-remaining") == "0":
        return True
    body = resp.text[:400].lower()
    return "rate limit" in body or "abuse detection" in body or "secondary rate" in body


def _parse_github_releases_atom(body: bytes, repo: str, *, include_prerelease: bool, limit: int) -> list[dict]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CollectorError("GitHub Releases Atom 返回内容不是有效 XML") from exc
    if xml_tag(root) != "feed":
        raise CollectorError("GitHub Releases Atom 格式无效")

    items: list[dict] = []
    seen_tags: set[str] = set()
    for entry in root:
        if xml_tag(entry) != "entry":
            continue
        links = [e for e in entry if xml_tag(e) == "link"]
        href = next((e.get("href") for e in links if e.get("rel", "alternate") == "alternate" and e.get("href")), "")
        href = href or xml_text(entry, "link")
        tag_match = _RELEASE_TAG_PATH_RE.search(href)
        tag_name = unquote(tag_match.group(1)) if tag_match else ""
        if not tag_name:
            entry_id = xml_text(entry, "id")
            tag_name = entry_id.rsplit("/", 1)[-1].strip() if "/" in entry_id else entry_id.strip()
        if not tag_name or tag_name in seen_tags:
            continue
        seen_tags.add(tag_name)

        title = xml_plain(xml_text(entry, "title"))
        prerelease = bool(_PRERELEASE_HINT_RE.search(tag_name) or _PRERELEASE_HINT_RE.search(title))
        if not include_prerelease and prerelease:
            continue

        author_el = xml_child(entry, "author")
        author_name = xml_text(author_el, "name") if author_el is not None else ""
        published_dt = feed_time(xml_text(entry, "published") or xml_text(entry, "updated"))
        content_html = xml_text(entry, "content", "summary")
        body_text = _atom_html_to_text(content_html)
        url = href or f"{GITHUB_WEB_ROOT}/{repo}/releases/tag/{quote(tag_name, safe='')}"
        items.append(
            {
                "id": git_item_id(repo, "release", tag_name),
                "platform": "github",
                "kind": "release",
                "repo": repo,
                "tag_name": tag_name,
                "name": title or tag_name,
                "body": body_text,
                "url": url,
                "published": published_dt.isoformat() if published_dt else None,
                "author": author_name or repo.split("/")[0],
                "prerelease": prerelease,
                "source_channel": "atom",
            }
        )
        if len(items) >= limit:
            break
    return items


def _parse_github_discussions_atom(body: bytes, repo: str, *, limit: int) -> list[dict]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CollectorError("GitHub Discussions Atom 返回内容不是有效 XML") from exc
    if xml_tag(root) != "feed":
        raise CollectorError("GitHub Discussions Atom 格式无效")

    items: list[dict] = []
    seen_ids: set[str] = set()
    for entry in root:
        if xml_tag(entry) != "entry":
            continue
        links = [e for e in entry if xml_tag(e) == "link"]
        href = next((e.get("href") for e in links if e.get("rel", "alternate") == "alternate" and e.get("href")), "")
        href = href or xml_text(entry, "link")
        if not href:
            continue
        num_match = _DISCUSSION_NUM_PATH_RE.search(href)
        ident = num_match.group(1) if num_match else hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
        ext_id = git_item_id(repo, "discussion", ident)
        if ext_id in seen_ids:
            continue
        seen_ids.add(ext_id)

        title = xml_plain(xml_text(entry, "title"))
        author_el = xml_child(entry, "author")
        author_name = xml_text(author_el, "name") if author_el is not None else ""
        published_dt = feed_time(xml_text(entry, "published") or xml_text(entry, "updated"))
        content_html = xml_text(entry, "content", "summary")
        body_text = _atom_html_to_text(content_html)
        items.append(
            {
                "id": ext_id,
                "platform": "github",
                "kind": "discussion",
                "repo": repo,
                "number": int(ident) if ident.isdigit() else None,
                "title": title,
                "body": body_text,
                "url": href,
                "published": published_dt.isoformat() if published_dt else None,
                "author": author_name or repo.split("/")[0],
                "source_channel": "atom",
            }
        )
        if len(items) >= limit:
            break
    return items


class _BaseGitRepoCollector:
    platform: Platform

    def __init__(self) -> None:
        settings = get_settings()
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_max_retries
        self.proxy_url: str | None = None
        self.source_config: dict[str, Any] = {}
        self.last_http_cache: dict[str, Any] | None = None
        self.not_modified: bool = False
        self.request_count: int = 0

    def _repo_cfg(self, target_type: str) -> dict:
        cfg = self.source_config if isinstance(self.source_config, dict) else {}
        raw = cfg.get("repo") if isinstance(cfg.get("repo"), dict) else cfg
        try:
            return normalize_repo_config(raw, target_type=target_type)
        except ValueError:
            return {}

    def _cached_etag(self, cache_key: str) -> str | None:
        cfg = self.source_config if isinstance(self.source_config, dict) else {}
        cache = cfg.get("_http_cache")
        if isinstance(cache, dict) and cache.get("key") == cache_key and cache.get("etag"):
            return str(cache["etag"])
        return None

    def _record_cache(self, cache_key: str, etag: str | None) -> None:
        if etag:
            self.last_http_cache = {"key": cache_key, "etag": etag}

    def _attach_source_meta(self, items: list[dict], repo: str, *, default_official: bool) -> list[dict]:
        cfg = self.source_config if isinstance(self.source_config, dict) else {}
        source_name = str(cfg.get("name") or "").strip() or repo
        is_official = bool(cfg.get("is_official")) if "is_official" in cfg else default_official
        for item in items:
            item["source_name"] = source_name
            item["is_official"] = is_official
        return items

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2**attempt, 8))
            self.request_count += 1
            try:
                resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    raise CollectorError(f"请求失败：{exc}") from exc
                continue
            if resp.status_code >= 500 and attempt < self._max_retries:
                last_exc = httpx.HTTPStatusError("server error", request=resp.request, response=resp)
                continue
            return resp
        raise CollectorError(f"请求失败：{last_exc}")


class GithubCollector(_BaseGitRepoCollector):
    platform = Platform.GITHUB

    def __init__(self, *, token: str | None = None) -> None:
        super().__init__()
        self._token = (get_settings().github_token if token is None else token).strip()
        self.last_trending_edition: dict[str, Any] | None = None

    def _api_headers(self, etag: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _atom_headers(self, etag: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/xml, */*;q=0.5",
        }
        if etag:
            headers["If-None-Match"] = etag
        return headers

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        self.not_modified = False
        self.last_http_cache = None
        self.last_trending_edition = None

        if target_type in ("trending", "gh_trending"):
            try:
                period, lang = parse_trending_target(target_value)
            except ValueError as exc:
                raise _fail_fast(str(exc)) from None
            items = await self._fetch_trending(period, language=lang, limit=max(int(limit or 25), 25))
            self._attach_source_meta(items, "GitHub Trending", default_official=False)
            return FetchResult(platform=self.platform, fetch_kind="github_trending", items=items)

        try:
            repo = normalize_repo_target("github", target_value)
        except ValueError as exc:
            raise _fail_fast(str(exc)) from None

        norm_type = {
            "releases": "releases",
            "gh_releases": "releases",
            "issues": "issues",
            "gh_issues": "issues",
            "discussions": "discussions",
            "gh_discussions": "discussions",
        }.get(target_type)
        if norm_type is None:
            raise _fail_fast(f"GitHub 不支持采集类型 {target_type}")

        repo_cfg = self._repo_cfg(norm_type)
        per_page = max(1, min(int(limit or 20), 50))
        if norm_type == "releases":
            include_pre = bool(repo_cfg.get("include_prerelease", True))
            items = await self._fetch_releases(repo, include_prerelease=include_pre, limit=per_page)
            self._attach_source_meta(items, repo, default_official=True)
            return FetchResult(platform=self.platform, fetch_kind="github_releases", items=items)
        if norm_type == "issues":
            labels = list(repo_cfg.get("labels") or [])
            items = await self._fetch_issues(repo, labels=labels, limit=per_page)
            self._attach_source_meta(items, repo, default_official=False)
            return FetchResult(platform=self.platform, fetch_kind="github_issues", items=items)
        items = await self._fetch_discussions(repo, limit=per_page)
        self._attach_source_meta(items, repo, default_official=False)
        return FetchResult(platform=self.platform, fetch_kind="github_discussions", items=items)

    async def _fetch_releases(self, repo: str, *, include_prerelease: bool, limit: int) -> list[dict]:
        api_cache_key = f"gh:rel:api:{repo.lower()}:{int(include_prerelease)}"
        atom_cache_key = f"gh:rel:atom:{repo.lower()}:{int(include_prerelease)}"
        async with httpx.AsyncClient(follow_redirects=True, **http_proxy_kwargs(self)) as client:
            api_url = f"{GITHUB_API_ROOT}/repos/{repo}/releases"
            try:
                resp = await self._request(
                    client,
                    api_url,
                    params={"per_page": min(max(limit * 2 if not include_prerelease else limit, 10), 50)},
                    headers=self._api_headers(self._cached_etag(api_cache_key)),
                )
            except CollectorError as exc:
                logger.warning("github releases api transport failed for %s (%s); falling back to atom", repo, exc)
                return await self._fetch_releases_atom(
                    client, repo, include_prerelease=include_prerelease, limit=limit, cache_key=atom_cache_key
                )

            if resp.status_code == 304:
                self.not_modified = True
                self._record_cache(api_cache_key, resp.headers.get("etag") or self._cached_etag(api_cache_key))
                return []
            if resp.status_code == 404:
                raise _fail_fast(f"GitHub 仓库 {repo} 不存在或未公开")
            if resp.status_code == 401:
                raise _fail_fast("GitHub 访问令牌无效或已过期")
            if _is_github_rate_limited(resp) or resp.status_code >= 500:
                logger.warning(
                    "github releases api returned %s for %s; falling back to public atom",
                    resp.status_code,
                    repo,
                )
                return await self._fetch_releases_atom(
                    client, repo, include_prerelease=include_prerelease, limit=limit, cache_key=atom_cache_key
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitHub Releases API 返回 {resp.status_code}")

            try:
                payload = resp.json()
            except ValueError as exc:
                raise CollectorError("GitHub Releases API 返回内容不是有效 JSON") from exc
            if not isinstance(payload, list):
                raise CollectorError("GitHub Releases API 返回结构异常")

            self._record_cache(api_cache_key, resp.headers.get("etag"))
            items: list[dict] = []
            for row in payload:
                if not isinstance(row, dict) or row.get("draft"):
                    continue
                prerelease = bool(row.get("prerelease"))
                if not include_prerelease and prerelease:
                    continue
                tag_name = str(row.get("tag_name") or "").strip()
                if not tag_name:
                    continue
                author = row.get("author") if isinstance(row.get("author"), dict) else {}
                assets = row.get("assets") if isinstance(row.get("assets"), list) else []
                published = row.get("published_at") or row.get("created_at") or ""
                url = str(row.get("html_url") or "").strip() or f"{GITHUB_WEB_ROOT}/{repo}/releases/tag/{quote(tag_name, safe='')}"
                items.append(
                    {
                        "id": git_item_id(repo, "release", tag_name),
                        "platform": "github",
                        "kind": "release",
                        "repo": repo,
                        "tag_name": tag_name,
                        "name": str(row.get("name") or "").strip() or tag_name,
                        "body": str(row.get("body") or "").strip(),
                        "url": url,
                        "published": published,
                        "author": str(author.get("login") or "").strip() or repo.split("/")[0],
                        "prerelease": prerelease,
                        "assets_count": len(assets),
                        "source_channel": "api",
                    }
                )
                if len(items) >= limit:
                    break
            return items

    async def _fetch_releases_atom(
        self,
        client: httpx.AsyncClient,
        repo: str,
        *,
        include_prerelease: bool,
        limit: int,
        cache_key: str,
    ) -> list[dict]:
        atom_url = f"{GITHUB_WEB_ROOT}/{repo}/releases.atom"
        resp = await self._request(client, atom_url, headers=self._atom_headers(self._cached_etag(cache_key)))
        if resp.status_code == 304:
            self.not_modified = True
            self._record_cache(cache_key, resp.headers.get("etag") or self._cached_etag(cache_key))
            return []
        if resp.status_code == 404:
            raise _fail_fast(f"GitHub 仓库 {repo} 不存在或未公开")
        if resp.status_code >= 400:
            raise CollectorError(f"GitHub Releases Atom 返回 {resp.status_code}")
        if len(resp.content) > MAX_ATOM_BYTES:
            raise CollectorError("GitHub Releases Atom 内容超过 5 MB")
        self._record_cache(cache_key, resp.headers.get("etag"))
        return _parse_github_releases_atom(resp.content, repo, include_prerelease=include_prerelease, limit=limit)

    async def _fetch_issues(self, repo: str, *, labels: list[str], limit: int) -> list[dict]:
        labels_sig = ",".join(sorted(l.casefold() for l in labels))
        cache_key = f"gh:iss:{repo.lower()}:{labels_sig}"
        params: dict[str, Any] = {
            "state": "all",
            "sort": "created",
            "direction": "desc",
            # GitHub /issues 会混入 PR，且多标签需要本地 OR 匹配，因此多拉一些余量。
            "per_page": min(max(limit * 2, 30), 100),
        }
        if len(labels) == 1:
            params["labels"] = labels[0]

        async with httpx.AsyncClient(follow_redirects=True, **http_proxy_kwargs(self)) as client:
            resp = await self._request(
                client,
                f"{GITHUB_API_ROOT}/repos/{repo}/issues",
                params=params,
                headers=self._api_headers(self._cached_etag(cache_key)),
            )
            if resp.status_code == 304:
                self.not_modified = True
                self._record_cache(cache_key, resp.headers.get("etag") or self._cached_etag(cache_key))
                return []
            if resp.status_code == 404:
                raise _fail_fast(f"GitHub 仓库 {repo} 不存在或未公开")
            if resp.status_code == 401:
                raise _fail_fast("GitHub 访问令牌无效或已过期")
            if _is_github_rate_limited(resp):
                raise CollectorDeferred(
                    "GitHub 匿名请求额度已用尽，将在额度重置后自动继续",
                    retry_at=_retry_at_from_headers(resp.headers, default_minutes=20),
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitHub Issues API 返回 {resp.status_code}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise CollectorError("GitHub Issues API 返回内容不是有效 JSON") from exc
            if not isinstance(payload, list):
                raise CollectorError("GitHub Issues API 返回结构异常")

            self._record_cache(cache_key, resp.headers.get("etag"))
            items: list[dict] = []
            for row in payload:
                if not isinstance(row, dict) or "pull_request" in row:
                    continue
                number = row.get("number")
                if number is None:
                    continue
                item_labels = _extract_label_names(row.get("labels"))
                if not _matches_labels(item_labels, labels):
                    continue
                user = row.get("user") if isinstance(row.get("user"), dict) else {}
                reactions = row.get("reactions") if isinstance(row.get("reactions"), dict) else {}
                url = str(row.get("html_url") or "").strip() or f"{GITHUB_WEB_ROOT}/{repo}/issues/{number}"
                items.append(
                    {
                        "id": git_item_id(repo, "issue", str(number)),
                        "platform": "github",
                        "kind": "issue",
                        "repo": repo,
                        "number": int(number),
                        "title": str(row.get("title") or "").strip(),
                        "body": str(row.get("body") or "").strip(),
                        "url": url,
                        "published": row.get("created_at") or row.get("updated_at") or "",
                        "author": str(user.get("login") or "").strip() or repo.split("/")[0],
                        "state": str(row.get("state") or "open"),
                        "labels": item_labels,
                        "comments": int(row.get("comments") or 0),
                        "reactions": int(reactions.get("total_count") or 0),
                        "source_channel": "api",
                    }
                )
                if len(items) >= limit:
                    break
            return items

    async def _fetch_discussions(self, repo: str, *, limit: int) -> list[dict]:
        cache_key = f"gh:disc:{repo.lower()}"
        async with httpx.AsyncClient(follow_redirects=True, **http_proxy_kwargs(self)) as client:
            resp = await self._request(
                client,
                f"{GITHUB_WEB_ROOT}/{repo}/discussions.atom",
                headers=self._atom_headers(self._cached_etag(cache_key)),
            )
            if resp.status_code == 304:
                self.not_modified = True
                self._record_cache(cache_key, resp.headers.get("etag") or self._cached_etag(cache_key))
                return []
            if resp.status_code == 404:
                raise _fail_fast(f"GitHub 仓库 {repo} 不存在、未公开或未开启 Discussions")
            if resp.status_code == 429:
                raise CollectorDeferred(
                    "GitHub Discussions 临时限流，稍后自动继续",
                    retry_at=_retry_at_from_headers(resp.headers, default_minutes=15),
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitHub Discussions Atom 返回 {resp.status_code}")
            if len(resp.content) > MAX_ATOM_BYTES:
                raise CollectorError("GitHub Discussions Atom 内容超过 5 MB")
            self._record_cache(cache_key, resp.headers.get("etag"))
            return _parse_github_discussions_atom(resp.content, repo, limit=limit)

    async def _fetch_trending(self, period: str, *, language: str = "", limit: int = 25) -> list[dict]:
        path = f"/trending/{quote(language, safe='')}" if language else "/trending"
        url = f"{GITHUB_WEB_ROOT}{path}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(follow_redirects=True, **http_proxy_kwargs(self)) as client:
            resp = await self._request(client, url, params={"since": period}, headers=headers)
            if resp.status_code == 404:
                raise _fail_fast(f"GitHub Trending 地址不存在（language={language or 'all'}）")
            if resp.status_code == 429:
                raise CollectorDeferred(
                    "GitHub Trending 临时限流，稍后自动继续",
                    retry_at=_retry_at_from_headers(resp.headers, default_minutes=15),
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitHub Trending 返回 {resp.status_code}")
            if len(resp.content) > MAX_ATOM_BYTES:
                raise CollectorError("GitHub Trending 页面体积超过 5 MB")

        raw_items, entries = parse_github_trending_html(
            resp.text, period=period, language=language, limit=limit
        )
        if not entries:
            raise CollectorError("GitHub Trending 页面未解析到有效仓库列表")
        self.last_trending_edition = {
            "period": period,
            "language": language,
            "entries": entries,
        }
        return raw_items


class GitcodeCollector(_BaseGitRepoCollector):
    platform = Platform.GITCODE

    def __init__(self, *, token: str | None = None) -> None:
        super().__init__()
        self._token = (get_settings().gitcode_token if token is None else token).strip()

    def _api_headers(self, etag: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if etag:
            headers["If-None-Match"] = etag
        return headers

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        self.not_modified = False
        self.last_http_cache = None
        try:
            repo = normalize_repo_target("gitcode", target_value)
        except ValueError as exc:
            raise _fail_fast(str(exc)) from None

        norm_type = {
            "releases": "releases",
            "gc_releases": "releases",
            "issues": "issues",
            "gc_issues": "issues",
        }.get(target_type)
        if norm_type is None:
            raise _fail_fast(f"GitCode 不支持采集类型 {target_type}")

        repo_cfg = self._repo_cfg(norm_type)
        per_page = max(1, min(int(limit or 20), 50))
        if norm_type == "releases":
            include_pre = bool(repo_cfg.get("include_prerelease", True))
            items = await self._fetch_releases(repo, include_prerelease=include_pre, limit=per_page)
            self._attach_source_meta(items, repo, default_official=True)
            return FetchResult(platform=self.platform, fetch_kind="gitcode_releases", items=items)
        labels = list(repo_cfg.get("labels") or [])
        items = await self._fetch_issues(repo, labels=labels, limit=per_page)
        self._attach_source_meta(items, repo, default_official=False)
        return FetchResult(platform=self.platform, fetch_kind="gitcode_issues", items=items)

    async def _fetch_releases(self, repo: str, *, include_prerelease: bool, limit: int) -> list[dict]:
        cache_key = f"gc:rel:{repo.lower()}:{int(include_prerelease)}"
        params: dict[str, Any] = {
            "per_page": min(max(limit * 2 if not include_prerelease else limit, 10), 50),
            "page": 1,
            "direction": "desc",
        }
        async with httpx.AsyncClient(follow_redirects=True, **http_proxy_kwargs(self)) as client:
            resp = await self._request(
                client,
                f"{GITCODE_API_ROOT}/repos/{repo}/releases",
                params=params,
                headers=self._api_headers(self._cached_etag(cache_key)),
            )
            if resp.status_code == 304:
                self.not_modified = True
                self._record_cache(cache_key, resp.headers.get("etag") or self._cached_etag(cache_key))
                return []
            if resp.status_code == 404:
                raise _fail_fast(f"GitCode 仓库 {repo} 不存在或未公开")
            if resp.status_code == 401:
                raise _fail_fast("GitCode 访问令牌无效或接口需要认证")
            if resp.status_code == 429:
                raise CollectorDeferred(
                    "GitCode 接口临时限流，稍后自动继续",
                    retry_at=_retry_at_from_headers(resp.headers, default_minutes=15),
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitCode Releases API 返回 {resp.status_code}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise CollectorError("GitCode Releases API 返回内容不是有效 JSON") from exc
            if not isinstance(payload, list):
                raise CollectorError("GitCode Releases API 返回结构异常")

            self._record_cache(cache_key, resp.headers.get("etag"))
            items: list[dict] = []
            for row in payload:
                if not isinstance(row, dict):
                    continue
                prerelease = bool(row.get("prerelease"))
                if not include_prerelease and prerelease:
                    continue
                tag_name = str(row.get("tag_name") or "").strip()
                if not tag_name:
                    continue
                author = row.get("author") if isinstance(row.get("author"), dict) else {}
                assets = row.get("assets") if isinstance(row.get("assets"), list) else []
                url = str(row.get("html_url") or "").strip() or f"{GITCODE_WEB_ROOT}/{repo}/releases/tag/{quote(tag_name, safe='')}"
                items.append(
                    {
                        "id": git_item_id(repo, "release", tag_name),
                        "platform": "gitcode",
                        "kind": "release",
                        "repo": repo,
                        "tag_name": tag_name,
                        "name": str(row.get("name") or "").strip() or tag_name,
                        "body": str(row.get("body") or "").strip(),
                        "url": url,
                        "published": row.get("published_at") or row.get("created_at") or "",
                        "author": str(author.get("name") or author.get("login") or "").strip() or repo.split("/")[0],
                        "prerelease": prerelease,
                        "assets_count": len(assets),
                        "source_channel": "api",
                    }
                )
                if len(items) >= limit:
                    break
            return items

    async def _fetch_issues(self, repo: str, *, labels: list[str], limit: int) -> list[dict]:
        labels_sig = ",".join(sorted(l.casefold() for l in labels))
        cache_key = f"gc:iss:{repo.lower()}:{labels_sig}"
        params: dict[str, Any] = {
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": min(max(limit * 2 if len(labels) > 1 else limit, 20), 100),
            "page": 1,
        }
        if len(labels) == 1:
            params["labels"] = labels[0]

        async with httpx.AsyncClient(follow_redirects=True, **http_proxy_kwargs(self)) as client:
            resp = await self._request(
                client,
                f"{GITCODE_API_ROOT}/repos/{repo}/issues",
                params=params,
                headers=self._api_headers(self._cached_etag(cache_key)),
            )
            if resp.status_code == 304:
                self.not_modified = True
                self._record_cache(cache_key, resp.headers.get("etag") or self._cached_etag(cache_key))
                return []
            if resp.status_code == 404:
                raise _fail_fast(f"GitCode 仓库 {repo} 不存在或未公开")
            if resp.status_code == 401:
                raise _fail_fast("GitCode 访问令牌无效或接口需要认证")
            if resp.status_code == 429:
                raise CollectorDeferred(
                    "GitCode 接口临时限流，稍后自动继续",
                    retry_at=_retry_at_from_headers(resp.headers, default_minutes=15),
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitCode Issues API 返回 {resp.status_code}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise CollectorError("GitCode Issues API 返回内容不是有效 JSON") from exc
            if not isinstance(payload, list):
                raise CollectorError("GitCode Issues API 返回结构异常")

            self._record_cache(cache_key, resp.headers.get("etag"))
            items: list[dict] = []
            for row in payload:
                if not isinstance(row, dict):
                    continue
                number = str(row.get("number") or row.get("id") or "").strip()
                if not number:
                    continue
                item_labels = _extract_label_names(row.get("labels"))
                if not _matches_labels(item_labels, labels):
                    continue
                user = row.get("user") if isinstance(row.get("user"), dict) else {}
                url = str(row.get("html_url") or "").strip() or f"{GITCODE_WEB_ROOT}/{repo}/issues/{quote(number, safe='')}"
                items.append(
                    {
                        "id": git_item_id(repo, "issue", number),
                        "platform": "gitcode",
                        "kind": "issue",
                        "repo": repo,
                        "number": int(number) if number.isdigit() else number,
                        "title": str(row.get("title") or "").strip(),
                        "body": str(row.get("body") or "").strip(),
                        "url": url,
                        "published": row.get("created_at") or row.get("updated_at") or "",
                        "author": str(user.get("name") or user.get("login") or "").strip() or repo.split("/")[0],
                        "state": str(row.get("state") or "open"),
                        "labels": item_labels,
                        "comments": int(row.get("comments") or 0),
                        "source_channel": "api",
                    }
                )
                if len(items) >= limit:
                    break
            return items
