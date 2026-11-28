"""GitHub 公开信源采集器：仓库版本发布（Releases）与趋势榜单（Trending）。

设计边界（见 docs/plans/2026-11-25-general-sources-and-reports.md 与 2026-11-27 收束规划）：
- 严格仅访问公开只读接口与原生 Atom 源，不处理私有仓库或登录态；
- Releases 优先走 REST API（带 If-None-Match 304 零配额消耗），遇到 403/429 匿名限流或
  上游故障时自动回退至无配额限制的 https://github.com/{owner}/{repo}/releases.atom；
- Trending 委托 app.collectors.github_trending 解析公开 SSR 榜单页面；
- 可选环境变量 ANISIGNAL_GITHUB_TOKEN 提额，留空默认纯匿名运行。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import html2text
import httpx

from app.clock import feed_time
from app.collectors.base import CollectorDeferred, CollectorError, USER_AGENT
from app.collectors.rss import child as xml_child, plain as xml_plain, tag as xml_tag, text as xml_text
from app.config import get_settings
from app.models.enums import Platform
from app.schemas.content import FetchResult
from app.schemas.ids import git_item_id
from app.services.collector_proxy import http_proxy_kwargs

logger = logging.getLogger(__name__)

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_WEB_ROOT = "https://github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$|^[A-Za-z0-9]$")
_PRERELEASE_HINT_RE = re.compile(r"(?:^|[.\-_+])(?:alpha|beta|rc|pre|preview|dev|nightly|canary|snapshot)\b", re.I)
_RELEASE_TAG_PATH_RE = re.compile(r"/releases/tag/([^/?#]+)")


def fail_fast(message: str) -> CollectorError:
    err = CollectorError(message)
    err.stop_fallback = True  # type: ignore[attr-defined]
    return err


def normalize_github_repo(raw: str) -> str:
    """将用户输入规整为 `owner/repo`；支持裸路径与公开 GitHub 仓库链接。"""
    value = str(raw or "").strip()
    if not value:
        raise ValueError("请填写 GitHub 仓库（如 owner/repo）")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value) or any(ch.isspace() for ch in value):
        raise ValueError("GitHub 仓库标识不能包含空白或控制字符")

    path = value
    lower = value.lower()
    if "://" in value or lower.startswith(("github.com/", "www.github.com/")):
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("请填写有效的公开 GitHub 仓库地址或 owner/repo")
        if host not in _GITHUB_HOSTS:
            raise ValueError("该链接不属于 GitHub，请填写 GitHub 仓库地址或 owner/repo")
        path = parsed.path

    parts = [seg for seg in path.strip("/").split("/") if seg]
    if len(parts) < 2:
        raise ValueError("请填写完整的 GitHub 仓库（如 owner/repo 或仓库链接）")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if ".." in owner or ".." in repo or not _SEGMENT_RE.fullmatch(owner) or not _SEGMENT_RE.fullmatch(repo):
        raise ValueError("GitHub 仓库格式无效，请使用 owner/repo 形式")
    full = f"{owner}/{repo}"
    if len(full) > 128:
        raise ValueError("GitHub 仓库路径过长（最多 128 字符）")
    return full


def normalize_repo_config(raw: dict | None, *, target_type: str = "releases") -> dict:
    """规范化仓库采集配置：仅 releases 支持 include_prerelease（默认包含预发布）。"""
    if not isinstance(raw, dict):
        return {}
    ttype = str(target_type or "").strip().lower()
    if ttype in {"releases", "gh_releases"}:
        if "include_prerelease" in raw and raw["include_prerelease"] is not None:
            if not bool(raw["include_prerelease"]):
                return {"include_prerelease": False}
    return {}


def repo_web_url(repo: str, kind: str = "releases") -> str:
    suffix = "releases" if kind in {"releases", "gh_releases"} else ""
    return f"{GITHUB_WEB_ROOT}/{repo}/{suffix}" if suffix else f"{GITHUB_WEB_ROOT}/{repo}"


def html_to_markdown_text(html_body: str) -> str:
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


def retry_at_from_headers(headers: httpx.Headers, *, default_minutes: int = 15) -> datetime:
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


def _is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    if resp.headers.get("x-ratelimit-remaining") == "0":
        return True
    body = resp.text[:400].lower()
    return "rate limit" in body or "abuse detection" in body or "secondary rate" in body


def _parse_releases_atom(body: bytes, repo: str, *, include_prerelease: bool, limit: int) -> list[dict]:
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
        body_text = html_to_markdown_text(xml_text(entry, "content", "summary"))
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


class GithubCollector:
    platform = Platform.GITHUB

    def __init__(self, *, token: str | None = None) -> None:
        settings = get_settings()
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_max_retries
        self._token = (settings.github_token if token is None else token).strip()
        self.proxy_url: str | None = None
        self.source_config: dict[str, Any] = {}
        self.last_http_cache: dict[str, Any] | None = None
        self.last_trending_edition: dict[str, Any] | None = None
        self.not_modified: bool = False
        self.request_count: int = 0

    def _repo_cfg(self, target_type: str) -> dict:
        cfg = self.source_config if isinstance(self.source_config, dict) else {}
        raw = cfg.get("repo") if isinstance(cfg.get("repo"), dict) else cfg
        return normalize_repo_config(raw, target_type=target_type)

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

    async def fetch(self, target_type: str, target_value: str, limit: int) -> FetchResult:
        self.not_modified = False
        self.last_http_cache = None
        self.last_trending_edition = None

        if target_type in ("trending", "gh_trending"):
            from app.collectors.github_trending import parse_trending_target

            try:
                period, lang = parse_trending_target(target_value)
            except ValueError as exc:
                raise fail_fast(str(exc)) from None
            items = await self._fetch_trending(period, language=lang, limit=max(int(limit or 25), 25))
            self._attach_source_meta(items, "GitHub Trending", default_official=False)
            return FetchResult(platform=self.platform, fetch_kind="github_trending", items=items)

        if target_type not in ("releases", "gh_releases"):
            raise fail_fast(f"GitHub 不支持采集类型 {target_type}")

        try:
            repo = normalize_github_repo(target_value)
        except ValueError as exc:
            raise fail_fast(str(exc)) from None

        repo_cfg = self._repo_cfg("releases")
        per_page = max(1, min(int(limit or 20), 50))
        include_pre = bool(repo_cfg.get("include_prerelease", True))
        items = await self._fetch_releases(repo, include_prerelease=include_pre, limit=per_page)
        self._attach_source_meta(items, repo, default_official=True)
        return FetchResult(platform=self.platform, fetch_kind="github_releases", items=items)

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
                raise fail_fast(f"GitHub 仓库 {repo} 不存在或未公开")
            if resp.status_code == 401:
                raise fail_fast("GitHub 访问令牌无效或已过期")
            if _is_rate_limited(resp) or resp.status_code >= 500:
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
            raise fail_fast(f"GitHub 仓库 {repo} 不存在或未公开")
        if resp.status_code >= 400:
            raise CollectorError(f"GitHub Releases Atom 返回 {resp.status_code}")
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise CollectorError("GitHub Releases Atom 内容超过 5 MB")
        self._record_cache(cache_key, resp.headers.get("etag"))
        return _parse_releases_atom(resp.content, repo, include_prerelease=include_prerelease, limit=limit)

    async def _fetch_trending(self, period: str, *, language: str = "", limit: int = 25) -> list[dict]:
        from app.collectors.github_trending import parse_github_trending_html

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
                raise fail_fast(f"GitHub Trending 地址不存在（language={language or 'all'}）")
            if resp.status_code == 429:
                raise CollectorDeferred(
                    "GitHub Trending 临时限流，稍后自动继续",
                    retry_at=retry_at_from_headers(resp.headers, default_minutes=15),
                )
            if resp.status_code >= 400:
                raise CollectorError(f"GitHub Trending 返回 {resp.status_code}")
            if len(resp.content) > MAX_RESPONSE_BYTES:
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
