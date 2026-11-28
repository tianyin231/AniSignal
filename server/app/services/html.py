"""外部正文 HTML 白名单消毒（服务层与公开接口共用唯一实现）。"""

from __future__ import annotations

import re

import bleach

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
