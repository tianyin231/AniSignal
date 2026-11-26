"""通用网页解析引擎：组合多款轻量级开源解析库（无头浏览器零依赖）。

组合栈：
- parsel (Scrapy, BSD-3-Clause)：CSS3 / XPath 选择器与节点裁剪；
- trafilatura + htmldate + courlan (Apache-2.0)：正文/元数据/发布时间抽取与 URL 规范化去噪；
- readability-lxml (Apache-2.0)：Mozilla Readability 算法兜底（补足短公告/表格/非文章页）；
- html2text (GPL-3.0)：确定性 HTML → 可读 Markdown/纯文本转换（与 urlwatch/webchanges 同源，适合逐行 Diff）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
import json
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from charset_normalizer import from_bytes as detect_charset
from courlan import clean_url, is_navigation_page, is_not_crawlable
from htmldate import find_date
import html2text
from lxml import html as lxml_html
from lxml.etree import ParserError, XPathError
from parsel import Selector
from readability import Document as ReadabilityDocument
import trafilatura
from trafilatura.settings import use_config as use_trafilatura_config

MAX_SELECTOR_LEN = 200
MAX_LINK_PATTERN_LEN = 200
MAX_IGNORE_PATTERNS = 10
MAX_IGNORE_PATTERN_LEN = 120
MAX_TEXT_CHARS = 50_000
MAX_MEDIA_ITEMS = 12

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_TRAILING_PERMALINK_RE = re.compile(r"[\s\u00b6#¶]+$")
_FILTER_QUERY_KEYS = frozenset({"label", "labels", "tag", "tags", "category", "page", "sort", "filter"})
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")
_CHARSET_HEADER_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.I)
_INLINE_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2}))?(?!\d)"
)
_ASSET_EXT_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|svg|ico|bmp|avif|mp4|webm|mp3|wav|ogg|pdf|zip|tar|gz|7z|rar|exe|dmg|apk|css|js|mjs|woff2?|ttf|eot|xml|json)(?:$|\?)",
    re.I,
)
_SPA_SHELL_RE = re.compile(
    r'(?:id=["\'](?:root|app|__next|__nuxt)["\']\s*>\s*</div|enable\s+javascript|javascript\s+is\s+required|请开启\s*javascript|需要启用\s*javascript|浏览器不支持\s*javascript)',
    re.I,
)
_BOILERPLATE_ANCHOR_TEXTS = frozenset({
    "home", "index", "about", "contact", "login", "sign in", "sign up", "register",
    "privacy", "privacy policy", "terms", "terms of service", "cookies", "sitemap",
    "next", "previous", "prev", "more", "read more", "learn more", "view all", "see all",
    "continue reading", "skip to content", "menu", "search", "rss", "atom", "feed",
    "首页", "主页", "关于", "关于我们", "联系我们", "登录", "注册", "隐私政策", "用户协议",
    "查看更多", "查看详情", "阅读全文", "展开", "更多", "下一页", "上一页", "返回顶部", "目录",
})
_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas")
_CHROME_TAGS = ("header", "nav", "footer", "aside", "form")
_FEED_MIME_TYPES = {
    "application/rss+xml": "rss",
    "application/atom+xml": "atom",
    "application/feed+json": "json",
    "application/json": "json",
}

_TRAF_CONFIG = use_trafilatura_config()
_TRAF_CONFIG.set("DEFAULT", "MIN_EXTRACTED_SIZE", "20")
_TRAF_CONFIG.set("DEFAULT", "MIN_OUTPUT_SIZE", "20")


class WebParseError(ValueError):
    """网页解析或选择器配置错误（可安全展示给管理员/用户）。"""


@dataclass(frozen=True)
class DetectedFeed:
    url: str
    title: str
    feed_type: str  # rss | atom | json


@dataclass(frozen=True)
class ExtractedPage:
    url: str
    title: str
    text: str
    published_at: datetime | None = None
    author: str | None = None
    site_name: str | None = None
    media: list[str] = field(default_factory=list)
    extractor: str = "trafilatura"
    detected_feeds: list[DetectedFeed] = field(default_factory=list)
    spa_warning: bool = False


@dataclass(frozen=True)
class DiscoveredLink:
    url: str
    title: str
    published_at: datetime | None = None
    excerpt: str = ""
    cover: str | None = None


def _is_xpath(expr: str) -> tuple[bool, str]:
    raw = expr.strip()
    if raw.lower().startswith("xpath:"):
        return True, raw[6:].strip()
    if raw.startswith(("/", "(/", "./", "../")):
        return True, raw
    return False, raw


def validate_selector(expr: str, *, label: str = "选择器") -> str:
    """校验 CSS 或 XPath 选择器语法，入库前拦截非法表达式。"""
    cleaned = expr.strip()
    if not cleaned:
        return ""
    if len(cleaned) > MAX_SELECTOR_LEN:
        raise WebParseError(f"{label}长度不能超过 {MAX_SELECTOR_LEN} 字符")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        raise WebParseError(f"{label}不能包含控制字符")
    probe = Selector(text="<html><body><div></div></body></html>")
    is_xp, body = _is_xpath(cleaned)
    if not body:
        raise WebParseError(f"{label}不能为空")
    try:
        if is_xp:
            probe.xpath(body)
        else:
            probe.css(body)
    except Exception as exc:  # noqa: BLE001 - cssselect / lxml 会抛出不同语法异常
        raise WebParseError(f"{label}语法无效：{exc}") from None
    return cleaned


def validate_pattern(expr: str, *, label: str = "匹配规则", max_len: int = MAX_LINK_PATTERN_LEN) -> str:
    cleaned = expr.strip()
    if not cleaned:
        return ""
    if len(cleaned) > max_len:
        raise WebParseError(f"{label}长度不能超过 {max_len} 字符")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        raise WebParseError(f"{label}不能包含控制字符")
    if cleaned.lower().startswith("regex:"):
        pattern = cleaned[6:].strip()
        if not pattern:
            raise WebParseError(f"{label}正则表达式不能为空")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise WebParseError(f"{label}正则表达式无效：{exc}") from None
    return cleaned


def normalize_web_config(raw: dict | None) -> dict:
    """规整并校验网页监控解析配置（供 Admin 目标与用户定制监控共用）。"""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise WebParseError("网页解析配置格式无效")
    allowed = {"selector", "exclude_selector", "link_pattern", "ignore_patterns", "fetch_body"}
    unknown = set(raw) - allowed
    if unknown:
        raise WebParseError(f"不支持的网页配置项：{', '.join(sorted(unknown))}")

    out: dict = {}
    if raw.get("selector"):
        out["selector"] = validate_selector(str(raw["selector"]), label="内容选择器")
    if raw.get("exclude_selector"):
        out["exclude_selector"] = validate_selector(str(raw["exclude_selector"]), label="排除选择器")
    if raw.get("link_pattern"):
        out["link_pattern"] = validate_pattern(str(raw["link_pattern"]), label="链接匹配规则")
    if raw.get("ignore_patterns") is not None:
        raw_ignores = raw["ignore_patterns"]
        if isinstance(raw_ignores, str):
            raw_ignores = [line.strip() for line in raw_ignores.splitlines() if line.strip()]
        if not isinstance(raw_ignores, list) or len(raw_ignores) > MAX_IGNORE_PATTERNS:
            raise WebParseError(f"忽略规则最多 {MAX_IGNORE_PATTERNS} 条")
        cleaned_ignores: list[str] = []
        for item in raw_ignores:
            val = validate_pattern(str(item), label="忽略行规则", max_len=MAX_IGNORE_PATTERN_LEN)
            if val and val not in cleaned_ignores:
                cleaned_ignores.append(val)
        if cleaned_ignores:
            out["ignore_patterns"] = cleaned_ignores
    if "fetch_body" in raw and raw["fetch_body"] is not None:
        out["fetch_body"] = bool(raw["fetch_body"])
    return out


def decode_html(body: bytes, content_type: str = "") -> str:
    """稳健解码网页字节流：优先响应头编码，回退 charset_normalizer 与 UTF-8。"""
    if not body:
        return ""
    match = _CHARSET_HEADER_RE.search(content_type or "")
    if match:
        encoding = match.group(1).strip()
        try:
            return body.decode(encoding, errors="replace").replace("\x00", "")
        except LookupError:
            pass
    best = detect_charset(body).best()
    if best is not None and best.encoding:
        try:
            return str(best).replace("\x00", "")
        except Exception:  # noqa: BLE001
            pass
    return body.decode("utf-8", errors="replace").replace("\x00", "")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    m = _INLINE_DATE_RE.search(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        try:
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def normalize_text(text: str, *, ignore_patterns: list[str] | tuple[str, ...] = ()) -> str:
    """规范化提取后的文本：统一换行/空白、剔除零宽字符并应用可选忽略行规则。"""
    if not text:
        return ""
    cleaned = unescape(text)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    compiled: list[tuple[bool, object]] = []
    for raw in ignore_patterns:
        token = str(raw or "").strip()
        if not token:
            continue
        if token.lower().startswith("regex:"):
            try:
                compiled.append((True, re.compile(token[6:].strip(), re.I)))
            except re.error:
                continue
        else:
            compiled.append((False, token.casefold()))

    lines: list[str] = []
    for raw_line in cleaned.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if compiled:
            lowered = stripped.casefold()
            if any(
                (bool(pat.search(stripped)) if is_re else (pat in lowered))
                for is_re, pat in compiled
            ):
                continue
        lines.append(line)
    result = "\n".join(lines).strip()
    result = _TRAILING_WS_RE.sub("\n", result)
    result = _MULTI_BLANK_RE.sub("\n\n", result)
    if len(result) > MAX_TEXT_CHARS:
        return result[:MAX_TEXT_CHARS]
    return result


def html_to_clean_text(html_fragment: str, base_url: str = "") -> str:
    """使用 html2text 将 HTML 片段转为适合逐行 Diff 的干净 Markdown/纯文本。"""
    if not html_fragment or not html_fragment.strip():
        return ""
    converter = html2text.HTML2Text(baseurl=base_url)
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_links = True
    converter.ignore_emphasis = True
    converter.bypass_tables = False
    converter.protect_links = False
    converter.unicode_snob = True
    converter.skip_internal_links = True
    converter.single_line_break = False
    try:
        rendered = converter.handle(html_fragment)
    except Exception:  # noqa: BLE001
        rendered = ""
    return normalize_text(rendered)


def detect_feeds(html_text: str, base_url: str) -> list[DetectedFeed]:
    """扫描页面 <link rel="alternate"> 声明的官方 RSS / Atom / JSON Feed。"""
    if not html_text:
        return []
    sel = Selector(text=html_text)
    feeds: list[DetectedFeed] = []
    seen: set[str] = set()
    for link in sel.css('link[rel*="alternate"][href]'):
        mime = (link.attrib.get("type") or "").split(";", 1)[0].strip().lower()
        href = (link.attrib.get("href") or "").strip()
        if not href:
            continue
        feed_type = _FEED_MIME_TYPES.get(mime)
        if feed_type is None:
            continue
        # 跳过普通 JSON API alternate（如 WP oEmbed）
        if mime == "application/json" and "feed" not in href.lower():
            continue
        abs_url = urljoin(base_url, href)
        if urlsplit(abs_url).scheme not in {"http", "https"} or abs_url in seen:
            continue
        seen.add(abs_url)
        title = (link.attrib.get("title") or "").strip() or feed_type.upper()
        feeds.append(DetectedFeed(url=abs_url, title=title[:120], feed_type=feed_type))
        if len(feeds) >= 5:
            break
    return feeds


def _parse_dom(html_text: str):
    try:
        return lxml_html.fromstring(html_text)
    except (ParserError, ValueError):
        return lxml_html.fromstring(f"<html><body>{html_text}</body></html>")


def _select_elements(root, expr: str) -> list:
    is_xp, body = _is_xpath(expr)
    try:
        if is_xp:
            matched = root.xpath(body)
            return [node for node in matched if hasattr(node, "tag")]
        return list(root.cssselect(body))
    except (XPathError, ValueError, Exception) as exc:  # noqa: BLE001
        raise WebParseError(f"选择器执行失败：{exc}") from None


def _drop_nodes(nodes: list) -> None:
    for node in nodes:
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)


def _strip_noise_tags(root) -> None:
    for tag_name in _NOISE_TAGS:
        _drop_nodes(list(root.xpath(f".//{tag_name}")))
    _drop_nodes(list(root.xpath(".//*[@hidden or @aria-hidden='true']")))


def _collect_images(elements: list, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for elem in elements:
        for img in elem.xpath(".//img"):
            raw_src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-original")
                or ""
            ).strip()
            if not raw_src or raw_src.startswith("data:"):
                continue
            abs_url = urljoin(base_url, raw_src)
            if urlsplit(abs_url).scheme not in {"http", "https"} or abs_url in seen:
                continue
            seen.add(abs_url)
            urls.append(abs_url)
            if len(urls) >= MAX_MEDIA_ITEMS:
                return urls
    return urls


def _extract_page_metadata(html_text: str, url: str) -> dict:
    sel = Selector(text=html_text)
    og_title = (sel.css('meta[property="og:title"]::attr(content)').get() or "").strip()
    doc_title = (sel.css("title::text").get() or "").strip()
    h1_title = " ".join(sel.css("h1 ::text").getall()).strip()
    og_site = (sel.css('meta[property="og:site_name"]::attr(content)').get() or "").strip()
    og_image = (sel.css('meta[property="og:image"]::attr(content)').get() or "").strip()
    meta_author = (
        sel.css('meta[name="author"]::attr(content)').get()
        or sel.css('meta[property="article:author"]::attr(content)').get()
        or ""
    ).strip()
    meta_date = (
        sel.css('meta[property="article:modified_time"]::attr(content)').get()
        or sel.css('meta[property="article:published_time"]::attr(content)').get()
        or sel.css('meta[name="publish-date"]::attr(content)').get()
        or sel.css("time[datetime]::attr(datetime)").get()
        or ""
    ).strip()

    traf_meta = None
    try:
        traf_meta = trafilatura.extract_metadata(html_text, default_url=url)
    except Exception:  # noqa: BLE001
        traf_meta = None

    title = (
        (traf_meta.title if traf_meta and traf_meta.title else "")
        or og_title
        or h1_title
        or doc_title
        or (urlsplit(url).hostname or url)
    )
    site_name = (
        (traf_meta.sitename if traf_meta and traf_meta.sitename else "")
        or og_site
        or (urlsplit(url).hostname or "")
    )
    author = (traf_meta.author if traf_meta and traf_meta.author else "") or meta_author or None
    image = (traf_meta.image if traf_meta and traf_meta.image else "") or og_image or ""
    if image:
        image = urljoin(url, image)
        if urlsplit(image).scheme not in {"http", "https"}:
            image = ""

    published_at = _parse_datetime(meta_date)
    if published_at is None and traf_meta and traf_meta.date:
        published_at = _parse_datetime(traf_meta.date)
    if published_at is None:
        try:
            found = find_date(html_text, url=url, original_date=False, outputformat="%Y-%m-%d")
            published_at = _parse_datetime(found)
        except Exception:  # noqa: BLE001
            published_at = None

    return {
        "title": _TRAILING_PERMALINK_RE.sub("", unescape(title).strip())[:240] or (urlsplit(url).hostname or url),
        "site_name": unescape(site_name).strip()[:120] or None,
        "author": unescape(author).strip()[:120] if author else None,
        "image": image or None,
        "published_at": published_at,
    }


def _has_meaningful_table(root) -> bool:
    for table in root.xpath(".//table"):
        rows = table.xpath(".//tr")
        if len(rows) >= 2 and len("".join(table.itertext()).strip()) >= 12:
            return True
    return False


def _looks_like_spa_shell(raw_html: str, extracted_text: str) -> bool:
    if len(extracted_text.strip()) >= 80:
        return False
    return bool(_SPA_SHELL_RE.search(raw_html))


def extract_page(
    body: bytes | str,
    url: str,
    *,
    content_type: str = "text/html",
    selector: str = "",
    exclude_selector: str = "",
    ignore_patterns: list[str] | tuple[str, ...] = (),
) -> ExtractedPage:
    """从公开网页提取结构化标题、干净正文、发布时间、图片与 RSS 探测结果。"""
    raw_text = decode_html(body, content_type) if isinstance(body, (bytes, bytearray)) else str(body or "")
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in {"text/plain", "text/markdown"}:
        cleaned = normalize_text(raw_text, ignore_patterns=ignore_patterns)
        first_line = next((ln.lstrip("# ").strip() for ln in cleaned.splitlines() if ln.strip()), "")
        return ExtractedPage(
            url=url,
            title=(first_line or urlsplit(url).hostname or url)[:240],
            text=cleaned,
            site_name=urlsplit(url).hostname,
            extractor="plain",
        )

    detected_feeds = detect_feeds(raw_text, url)
    meta = _extract_page_metadata(raw_text, url)
    root = _parse_dom(raw_text)
    _strip_noise_tags(root)
    if exclude_selector:
        excluded = _select_elements(root, exclude_selector)
        _drop_nodes(excluded)

    media: list[str] = []
    if meta["image"]:
        media.append(meta["image"])

    # 路径 1：显式 CSS / XPath 选择器精准监控
    if selector:
        matched = _select_elements(root, selector)
        if not matched:
            raise WebParseError(f"页面未匹配到选择器：{selector}")
        fragments = [lxml_html.tostring(node, encoding="unicode") for node in matched]
        text = normalize_text(
            html_to_clean_text("\n".join(fragments), base_url=url),
            ignore_patterns=ignore_patterns,
        )
        for img_url in _collect_images(matched, url):
            if img_url not in media and len(media) < MAX_MEDIA_ITEMS:
                media.append(img_url)
        return ExtractedPage(
            url=url,
            title=meta["title"],
            text=text,
            published_at=meta["published_at"],
            author=meta["author"],
            site_name=meta["site_name"],
            media=media,
            extractor="selector",
            detected_feeds=detected_feeds,
            spa_warning=_looks_like_spa_shell(raw_text, text),
        )

    # 路径 2：自动多引擎级联（trafilatura → readability-lxml → 去噪 DOM）
    cleaned_html = lxml_html.tostring(root, encoding="unicode")
    has_table = _has_meaningful_table(root)

    traf_text = ""
    try:
        doc = trafilatura.bare_extraction(
            cleaned_html,
            url=url,
            include_tables=True,
            include_formatting=True,
            include_links=False,
            include_comments=False,
            favor_recall=True,
            with_metadata=True,
            config=_TRAF_CONFIG,
        )
        if doc is not None and getattr(doc, "text", None):
            traf_text = normalize_text(doc.text, ignore_patterns=ignore_patterns)
    except Exception:  # noqa: BLE001
        traf_text = ""

    readability_text = ""
    need_readability = (
        len(traf_text) < 80
        or (has_table and "|" not in traf_text)
    )
    if need_readability:
        try:
            summary_html = ReadabilityDocument(cleaned_html).summary(html_partial=True)
            readability_text = normalize_text(
                html_to_clean_text(summary_html, base_url=url),
                ignore_patterns=ignore_patterns,
            )
        except Exception:  # noqa: BLE001
            readability_text = ""

    # 选优：若 trafilatura 漏掉表格而 readability 完整保留，或 trafilatura 过短时切 readability
    if traf_text and not (
        readability_text
        and (
            (has_table and "|" not in traf_text and "|" in readability_text)
            or (len(traf_text) < 80 and len(readability_text) > len(traf_text) + 40)
        )
    ):
        chosen_text, extractor = traf_text, "trafilatura"
    elif readability_text:
        chosen_text, extractor = readability_text, "readability"
    else:
        for chrome in _CHROME_TAGS:
            _drop_nodes(list(root.xpath(f".//{chrome}")))
        containers = root.xpath(".//main | .//article | .//*[@role='main']")
        target_nodes = containers if containers else (root.xpath(".//body") or [root])
        dom_html = "\n".join(lxml_html.tostring(n, encoding="unicode") for n in target_nodes)
        chosen_text = normalize_text(
            html_to_clean_text(dom_html, base_url=url),
            ignore_patterns=ignore_patterns,
        )
        extractor = "dom"

    # 若正文仍为空且页面含有效表格（例如极简定价表），直接从去噪主区域补提一次
    if not chosen_text and has_table:
        dom_html = lxml_html.tostring(root, encoding="unicode")
        chosen_text = normalize_text(html_to_clean_text(dom_html, base_url=url), ignore_patterns=ignore_patterns)
        extractor = "dom"

    for img_url in _collect_images([root], url):
        if img_url not in media and len(media) < MAX_MEDIA_ITEMS:
            media.append(img_url)

    return ExtractedPage(
        url=url,
        title=meta["title"],
        text=chosen_text,
        published_at=meta["published_at"],
        author=meta["author"],
        site_name=meta["site_name"],
        media=media,
        extractor=extractor,
        detected_feeds=detected_feeds,
        spa_warning=_looks_like_spa_shell(raw_text, chosen_text),
    )


def _match_link_pattern(url: str, link_pattern: str) -> bool:
    if not link_pattern:
        return True
    if link_pattern.lower().startswith("regex:"):
        try:
            return bool(re.search(link_pattern[6:].strip(), url, re.I))
        except re.error:
            return False
    parsed = urlsplit(url)
    path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    if link_pattern.startswith("/"):
        return path_and_query.startswith(link_pattern) or link_pattern in parsed.path
    return link_pattern.casefold() in url.casefold()


def _canonical_article_url(raw_href: str, base_url: str) -> str | None:
    if not raw_href:
        return None
    href = raw_href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    abs_url = urljoin(base_url, href)
    parsed = urlsplit(abs_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if _ASSET_EXT_RE.search(parsed.path):
        return None
    try:
        cleaned = clean_url(abs_url)
    except Exception:  # noqa: BLE001
        cleaned = abs_url
    cleaned = urldefrag(cleaned or abs_url)[0]
    return cleaned or abs_url


def _extract_jsonld_links(html_text: str, base_url: str, *, link_pattern: str) -> list[DiscoveredLink]:
    sel = Selector(text=html_text)
    results: list[DiscoveredLink] = []
    seen: set[str] = set()
    base_clean = _canonical_article_url(base_url, base_url) or base_url

    def visit(obj) -> None:
        if isinstance(obj, list):
            for entry in obj:
                visit(entry)
            return
        if not isinstance(obj, dict):
            return
        node_type = obj.get("@type")
        types = {t.lower() for t in (node_type if isinstance(node_type, list) else [node_type]) if isinstance(t, str)}
        if types & {"itemlist", "blog", "collectionpage"}:
            for key in ("itemListElement", "blogPost", "hasPart", "mainEntity"):
                if key in obj:
                    visit(obj[key])
        if types & {"listitem", "newsarticle", "blogposting", "article", "posting", "creativework"}:
            inner = obj.get("item") if isinstance(obj.get("item"), dict) else obj
            raw_url = inner.get("url") or inner.get("@id") or obj.get("url") or ""
            if isinstance(raw_url, str):
                canon = _canonical_article_url(raw_url, base_url)
                if canon and canon != base_clean and canon not in seen and _match_link_pattern(canon, link_pattern):
                    title = str(inner.get("headline") or inner.get("name") or obj.get("name") or "").strip()
                    if len(title) >= 4:
                        seen.add(canon)
                        pub = _parse_datetime(
                            str(inner.get("datePublished") or inner.get("dateModified") or "")
                        )
                        img = inner.get("image")
                        cover = None
                        if isinstance(img, str):
                            cover = urljoin(base_url, img)
                        elif isinstance(img, dict) and isinstance(img.get("url"), str):
                            cover = urljoin(base_url, img["url"])
                        elif isinstance(img, list) and img and isinstance(img[0], str):
                            cover = urljoin(base_url, img[0])
                        if cover and urlsplit(cover).scheme not in {"http", "https"}:
                            cover = None
                        results.append(
                            DiscoveredLink(
                                url=canon,
                                title=unescape(title)[:240],
                                published_at=pub,
                                excerpt=unescape(str(inner.get("description") or "")).strip()[:400],
                                cover=cover,
                            )
                        )
        if "@graph" in obj:
            visit(obj["@graph"])

    for script_body in sel.css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(script_body)
        except ValueError:
            continue
        visit(data)
    return results


def _find_card_context(anchor) -> tuple[str, datetime | None, str | None]:
    """向上查找同一文章卡片（article/li/tr/div），补全标题、发布日期与缩略图。"""
    title = " ".join(anchor.itertext()).strip()
    title = re.sub(r"\s+", " ", title)
    pub = None
    cover = None
    container = anchor
    for _ in range(3):
        parent = container.getparent()
        if parent is None:
            break
        tag = str(getattr(parent, "tag", "")).lower()
        if tag in {"article", "li", "tr", "section"}:
            container = parent
            break
        # 避免跨到大容器（含大量链接）误取其他文章的时间/标题
        if len(parent.xpath(".//a[@href]")) <= 4:
            container = parent
        else:
            break

    if not title or title.casefold() in _BOILERPLATE_ANCHOR_TEXTS or len(title) < 4:
        for heading in container.xpath(".//h1 | .//h2 | .//h3 | .//h4"):
            htext = re.sub(r"\s+", " ", " ".join(heading.itertext())).strip()
            if len(htext) >= 4:
                title = htext
                break

    for time_el in container.xpath(".//time"):
        pub = _parse_datetime(time_el.get("datetime") or "".join(time_el.itertext()))
        if pub:
            break
    if pub is None:
        snippet = " ".join(container.itertext())[:400]
        pub = _parse_datetime(snippet)

    imgs = container.xpath(".//img")
    if imgs:
        raw_src = (imgs[0].get("src") or imgs[0].get("data-src") or "").strip()
        if raw_src and not raw_src.startswith("data:"):
            cover = raw_src
    return title, pub, cover


def extract_list(
    body: bytes | str,
    url: str,
    *,
    content_type: str = "text/html",
    selector: str = "",
    exclude_selector: str = "",
    link_pattern: str = "",
    limit: int = 20,
) -> tuple[list[DiscoveredLink], dict]:
    """从列表页（博客/公告列表/新闻目录）发现文章链接列表，并返回页面元信息。"""
    raw_text = decode_html(body, content_type) if isinstance(body, (bytes, bytearray)) else str(body or "")
    detected_feeds = detect_feeds(raw_text, url)
    meta = _extract_page_metadata(raw_text, url)
    base_clean = _canonical_article_url(url, url) or url
    base_host = (urlsplit(base_clean).hostname or "").lower()

    results: list[DiscoveredLink] = []
    seen: set[str] = set()

    # 若未指定局部选择器，优先吸收 JSON-LD 中的结构化文章列表
    if not selector:
        for item in _extract_jsonld_links(raw_text, url, link_pattern=link_pattern):
            if item.url not in seen:
                seen.add(item.url)
                results.append(item)

    root = _parse_dom(raw_text)
    _strip_noise_tags(root)
    if exclude_selector:
        _drop_nodes(_select_elements(root, exclude_selector))

    explicit_rule = bool(selector or link_pattern)
    if selector:
        scopes = _select_elements(root, selector)
        if not scopes:
            raise WebParseError(f"列表页未匹配到选择器：{selector}")
    else:
        for chrome in _CHROME_TAGS:
            _drop_nodes(list(root.xpath(f".//{chrome}")))
        scopes = root.xpath(".//main | .//*[@role='main']")
        if not scopes or not any(scope.xpath(".//a[@href]") for scope in scopes):
            scopes = root.xpath(".//body") or [root]

    min_title_len = 2 if explicit_rule else 5
    for scope in scopes:
        anchors = [scope] if str(getattr(scope, "tag", "")).lower() == "a" else scope.xpath(".//a[@href]")
        for anchor in anchors:
            canon = _canonical_article_url(anchor.get("href") or "", url)
            if not canon or canon == base_clean or canon in seen:
                continue
            if not _match_link_pattern(canon, link_pattern):
                continue
            # 过滤常见的标签/分类/分页筛选链接（如 ?label=copilot），除非用户显式匹配查询参数
            parsed_cand = urlsplit(canon)
            if parsed_cand.query and "?" not in link_pattern:
                q_keys = {part.split("=", 1)[0].lower() for part in parsed_cand.query.split("&") if part}
                if q_keys and q_keys.issubset(_FILTER_QUERY_KEYS):
                    continue
            # 无显式规则时，用 courlan 过滤掉分页/登录/标签/站外导航等非内容链接
            if not explicit_rule:
                cand_host = (urlsplit(canon).hostname or "").lower()
                if base_host and cand_host and cand_host != base_host and not cand_host.endswith("." + base_host):
                    continue
                if is_not_crawlable(canon) or is_navigation_page(canon):
                    continue
                parsed = urlsplit(canon)
                if parsed.path in {"", "/"} and not parsed.query:
                    continue
            title, pub, raw_cover = _find_card_context(anchor)
            title = unescape(title).strip()
            if len(title) < min_title_len or title.casefold() in _BOILERPLATE_ANCHOR_TEXTS:
                continue
            cover = None
            if raw_cover:
                abs_cov = urljoin(url, raw_cover)
                if urlsplit(abs_cov).scheme in {"http", "https"}:
                    cover = abs_cov
            seen.add(canon)
            results.append(
                DiscoveredLink(
                    url=canon,
                    title=title[:240],
                    published_at=pub,
                    cover=cover,
                )
            )
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    meta["detected_feeds"] = detected_feeds
    meta["spa_warning"] = len(results) == 0 and bool(_SPA_SHELL_RE.search(raw_text))
    return results[:limit], meta
