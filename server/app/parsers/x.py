"""X 匿名公开 HTML 解析；引用帖不充当原帖，发布时间只取原文。"""

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "2"
_STATUS_PATH = re.compile(r"/([A-Za-z0-9_]+)/status/(\d+)")
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())


def _is_post(node):
    # 匿名页改版后去掉了 data-timeline-entry，原帖与引用仍各有完整的 data-href。
    return bool(_STATUS_PATH.fullmatch(node.attrs.get("data-href", "")))


def normalize_account(value: str) -> str:
    value = value.strip()
    if "://" in value:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or url.netloc.lower() not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            raise ValueError("填写 X 账号名或 x.com 账号主页地址")
        value = url.path.strip("/")
    value = value.removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,15}", value):
        raise ValueError("X 账号名只能包含 1 至 15 位字母、数字或下划线")
    return value


class _Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def walk(self, *, own=False):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                if own and _is_post(child):
                    continue
                yield from child.walk(own=own)

    def text(self):
        return "".join(child if isinstance(child, str) else child.text() for child in self.children
                       if isinstance(child, str) or child.tag not in {"script", "style"})


class _Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.root = _Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _item(node, path):
    match = _STATUS_PATH.fullmatch(path)
    if not match:
        return None
    own = list(node.walk(own=True))
    bodies = [n.text() for n in own if n.attrs.get("dir") == "auto"
              and "whitespace-pre-wrap" in n.attrs.get("class", "")]
    scripts = ["".join(c for c in n.children if isinstance(c, str)) for n in own if n.tag == "script"]
    dates = [re.search(r'"timestamp":(\d+)', script) for script in scripts
             if "formatRelativePastTimestamp" in script or "formatFullTimestamp" in script]
    published = next((int(stamp[1]) / 1000 for stamp in dates if stamp), None)
    text = bodies[0] if bodies else ""
    media = [n.attrs.get("src", "") for n in own if n.tag == "img" and n.attrs.get("src", "").startswith(
        ("https://pbs.twimg.com/media/", "https://pbs.twimg.com/ext_tw_video_thumb/", "https://pbs.twimg.com/amplify_video_thumb/"))]
    return {
        "id": match[2], "author": match[1], "url": "https://x.com" + path,
        "published_at": datetime.fromtimestamp(published, timezone.utc).isoformat() if published else None,
        "text": text.removesuffix("Show more").rstrip(), "truncated": "Show more" in text,
        "pinned": node.text().startswith("Pinned"), "media": list(dict.fromkeys(media)),
        "quoted_urls": list(dict.fromkeys("https://x.com" + n.attrs["data-href"] for n in node.walk()
            if n is not node and _STATUS_PATH.fullmatch(n.attrs.get("data-href", "")))),
        "links": list(dict.fromkeys(n.attrs["href"] for n in own if n.tag == "a"
            and n.attrs.get("href", "").startswith(("https://", "http://")))),
        "text_scope": "post",
    }


def parse_page(source: str, post_url: str | None = None) -> list[dict]:
    doc = _Document(source)
    if post_url:
        path = urlsplit(post_url).path
        canonical = next((n.attrs.get("content", "") for n in doc.root.walk()
                          if n.tag == "meta" and n.attrs.get("property") == "og:url"), "")
        if urlsplit(canonical).path != path:
            return []
        article = next((n for n in doc.root.walk() if n.tag == "article"), None)
        item = _item(article, path) if article else None
        return [{**item, "text_scope": "full_post"}] if item else []
    rows = []
    for node in doc.root.walk():
        if not _is_post(node):
            continue
        parent = node.parent
        while parent and not _is_post(parent):
            parent = parent.parent
        if parent is None and (item := _item(node, node.attrs.get("data-href", ""))):
            rows.append(item)
    return list({row["id"]: row for row in rows}.values())


def parse_post(raw: dict) -> NormalizedContent:
    from app.config import get_settings

    author = str(raw.get("author") or "").lower()
    official = {account.strip().lower(): label.strip() for part in get_settings().official_x_accounts.split(",")
                if ":" in part for account, label in [part.split(":", 1)]}
    label = official.get(author)
    text = str(raw.get("text") or "").strip()
    published = datetime.fromisoformat(raw["published_at"]) if raw.get("published_at") else None
    return NormalizedContent(
        platform=Platform.X, content_type=ContentType.POST, external_id=str(raw.get("id") or ""),
        author=NormalizedAuthor(external_id=author or None, name="@" + author if author else None,
            official_title=label, identity=Identity.OFFICIAL if label else Identity.USER),
        title=" ".join(text.split())[:160] or "X 原帖", text=text or None,
        url=raw.get("url"), published_at=published, media=raw.get("media") or [],
        extra={key: raw.get(key) for key in ("truncated", "text_scope", "quoted_urls", "links")},
    )
