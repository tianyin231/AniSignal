"""把小黑盒公开分享链接记录为一条玩家内容引用。"""

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"
_POST_PATH = "/app/bbs/link/"
_PROFILE_PATH = "/app/user/profile/"
_WEB_HOSTS = {"xiaoheihe.cn", "www.xiaoheihe.cn"}


def account_target(value: str) -> str:
    """接受数字 UID 和官网分享的主页链接，保留待解析的分享 ID。"""
    value = value.strip()
    if "://" in value:
        url = urlsplit(value)
        if url.scheme != "https" or url.netloc not in _WEB_HOSTS or not url.path.startswith(_PROFILE_PATH):
            raise ValueError("请填写小黑盒 UID 或公开个人主页链接")
        value = url.path.removeprefix(_PROFILE_PATH).rstrip("/")
    if re.fullmatch(r"[0-9]{1,20}", value) and int(value):
        return str(int(value))
    if re.fullmatch(r"[0-9a-fA-F]{12}", value) and not value.isdecimal():
        return value.lower()
    raise ValueError("请填写小黑盒 UID 或公开个人主页链接")


def account_url(uid: str) -> str:
    return f"https://www.xiaoheihe.cn{_PROFILE_PATH}{uid}"


def parse_share(raw: dict) -> NormalizedContent:
    url = urlsplit(raw["url"].strip())
    link_id = ""
    if url.scheme == "https":
        if url.netloc in _WEB_HOSTS and url.path.startswith(_POST_PATH):
            link_id = url.path.removeprefix(_POST_PATH).rstrip("/")
        elif url.netloc == "api.xiaoheihe.cn" and url.path == "/v3/bbs/app/api/web/share":
            ids = parse_qs(url.query).get("link_id", [])
            if len(ids) == 1:
                link_id = ids[0]
    if not re.fullmatch(r"[a-zA-Z0-9]{1,48}", link_id):
        raise ValueError("请填写小黑盒公开帖子链接")
    return NormalizedContent(
        platform=Platform.XIAOHEIHE,
        content_type=ContentType.POST,
        external_id=link_id,
        url=f"https://www.xiaoheihe.cn{_POST_PATH}{link_id}",
        title=raw["title"],
        published_at=raw.get("published_at"),
        metrics={key: raw[key] for key in ("like", "reply") if raw.get(key) is not None},
        # 分享链接只提供作者昵称，引用条目不推断跨帖账号身份。
        author=NormalizedAuthor(external_id=f"share:{link_id}", name=raw["author"], identity=Identity.USER),
    )


def parse_account_post(raw: dict, author: NormalizedAuthor) -> NormalizedContent | None:
    if str(raw.get("userid")) != author.external_id:
        return None
    title = str(raw.get("title") or raw.get("description") or "").strip()
    share_url = raw.get("share_url")
    if not title or not isinstance(share_url, str):
        return None
    try:
        post = parse_share({"url": share_url, "title": title[:240], "author": author.name})
    except ValueError:
        return None
    post.author = author
    try:
        post.published_at = datetime.fromtimestamp(int(raw["create_at"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    return post
