"""GitHub Trending SSR 榜单解析与匿名 README 抓取清洗模块（零 API 配额消耗）。"""

from __future__ import annotations

import asyncio
from datetime import timezone
import logging
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from parsel import Selector

from app.clock import utcnow
from app.collectors.base import USER_AGENT
from app.collectors.github import (
    GITHUB_WEB_ROOT,
    git_item_id,
    html_to_markdown_text,
    normalize_github_repo,
)
from app.services.collector_proxy import http_proxy_kwargs

logger = logging.getLogger(__name__)

GITHUB_RAW_ROOT = "https://raw.githubusercontent.com"
TRENDING_PERIODS = ("daily", "weekly", "monthly")
MAX_README_BYTES = 512 * 1024
MAX_README_CHARS = 8000

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_TRENDING_LANG_RE = re.compile(r"^[a-z0-9][a-z0-9.+#_-]{0,31}$")
_INT_RE = re.compile(r"[\d,]+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_MD_BADGE_LINK_RE = re.compile(r"\[!\[[^\]]*\]\([^)]+\)\]\([^)]+\)")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_HTML_TAG_RE = re.compile(
    r"</?(?:div|p|span|img|picture|source|center|br|a|table|tr|td|th|thead|tbody|details|summary|h[1-6]|sub|sup|kbd|hr)\b[^>]*>",
    re.I,
)


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
        if parsed.scheme not in {"http", "https"} or host not in _GITHUB_HOSTS:
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
            repo = normalize_github_repo(href.lstrip("/"))
        except ValueError:
            continue
        rkey = repo.lower()
        if rkey in seen:
            continue
        seen.add(rkey)

        rank = len(entries) + 1
        desc = " ".join(part.strip() for part in article.css("p::text").getall() if part.strip())
        lang = (article.css('[itemprop="programmingLanguage"]::text').get() or "").strip()

        stars = _parse_int_text(" ".join(article.css('a[href$="/stargazers"]::text').getall()))
        forks = _parse_int_text(" ".join(article.css('a[href$="/forks"]::text').getall()))
        if not stars and not forks:
            muted_nums = [_parse_int_text(t) for t in article.css("a.Link--muted::text").getall() if t.strip()]
            muted_nums = [n for n in muted_nums if n > 0]
            if muted_nums:
                stars = muted_nums[0]
            if len(muted_nums) > 1:
                forks = muted_nums[1]

        period_stars = _parse_int_text(" ".join(article.css("span.float-sm-right::text").getall()))
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


def clean_readme_markdown(raw: str, *, max_chars: int = MAX_README_CHARS) -> str:
    """清洗 GitHub README 中的徽章图、HTML 排版壳和连续空行，保留可读正文与代码说明。"""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""
    text = _HTML_COMMENT_RE.sub("", text)
    text = _MD_BADGE_LINK_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _HTML_TAG_RE.sub("\n", text)
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = line.rstrip()
        stripped = cleaned.strip()
        if stripped and re.fullmatch(r"[\s|•·\-—_=/\\]+", stripped) and not stripped.startswith("---"):
            continue
        lines.append(cleaned)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(out) <= max_chars:
        return out
    cut = out[:max_chars]
    last_nl = cut.rfind("\n")
    if last_nl >= max_chars // 2:
        cut = cut[:last_nl]
    return cut.rstrip() + "\n…"


async def fetch_github_repo_readme(
    repo: str,
    *,
    proxy_url: str | None = None,
    timeout: float = 12.0,
    max_chars: int = MAX_README_CHARS,
) -> str:
    """匿名抓取 GitHub 仓库 README（零 API 配额消耗）：优先 raw CDN，回退网页 SSR 正文。"""
    try:
        clean_repo = normalize_github_repo(repo)
    except ValueError:
        return ""

    headers = {"User-Agent": USER_AGENT, "Accept": "text/plain, text/markdown, text/html;q=0.8"}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            **http_proxy_kwargs(proxy_url),
        ) as client:
            for candidate in ("README.md", "readme.md", "README.rst"):
                url = f"{GITHUB_RAW_ROOT}/{clean_repo}/HEAD/{candidate}"
                try:
                    resp = await client.get(url, headers=headers)
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200 and resp.content:
                    raw_text = resp.content[:MAX_README_BYTES].decode("utf-8", errors="replace")
                    cleaned = clean_readme_markdown(raw_text, max_chars=max_chars)
                    if cleaned:
                        return cleaned

            try:
                page = await client.get(f"{GITHUB_WEB_ROOT}/{clean_repo}", headers=headers)
            except httpx.HTTPError:
                return ""
            if page.status_code == 200 and page.text:
                sel = Selector(text=page.text)
                article_html = sel.css("article.markdown-body").get() or ""
                if article_html:
                    return clean_readme_markdown(html_to_markdown_text(article_html), max_chars=max_chars)
    except Exception:  # noqa: BLE001 - 匿名补全失败不影响主流程
        logger.debug("github readme fetch skipped for %s", clean_repo, exc_info=True)
    return ""


async def enrich_trending_items_with_readme(
    items: list[dict],
    *,
    skip_repos: set[str] | None = None,
    proxy_url: str | None = None,
    concurrency: int = 4,
) -> int:
    """为新上榜且尚未入库 README 的 GitHub 仓库并发匿名补全 README 精读正文。"""
    skipped = {r.lower() for r in (skip_repos or set())}
    targets = [
        item
        for item in items
        if item.get("kind") == "trending"
        and item.get("repo")
        and str(item["repo"]).lower() not in skipped
        and not item.get("readme")
    ]
    if not targets:
        return 0

    sem = asyncio.Semaphore(max(1, concurrency))
    enriched = 0

    async def _one(item: dict) -> None:
        nonlocal enriched
        async with sem:
            readme = await fetch_github_repo_readme(str(item["repo"]), proxy_url=proxy_url)
            if readme:
                item["readme"] = readme
                enriched += 1

    await asyncio.gather(*(_one(item) for item in targets), return_exceptions=True)
    return enriched
