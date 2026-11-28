"""外部正文 HTML 白名单消毒与 Markdown 转换（服务层、采集器与公开接口共用唯一实现）。"""

from __future__ import annotations

from html import unescape
import re

import bleach
import html2text

_ALLOWED_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "div", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "q",
    "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "th",
    "thead", "tr", "u", "ul",
]
_ALLOWED_ATTRS = {"a": ["href", "title"], "img": ["src", "alt"]}


def sanitize_html(text: str) -> str | None:
    """对外部 HTML 做白名单清洗并为图片补齐 no-referrer 与懒加载。"""
    if "<" not in text or ">" not in text:
        return None
    cleaned = bleach.clean(text, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
    return re.sub(r"<img\b", '<img referrerpolicy="no-referrer" loading="lazy"', cleaned)


def html_to_markdown_text(html_body: str) -> str:
    """将 HTML 片段转为干净 Markdown 文本，空结果时退回纯文本去标签。"""
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
    return text or unescape(bleach.clean(raw, tags=[], strip=True)).strip()
