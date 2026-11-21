"""小黑盒公开帖子与账号动态解析器。"""

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent
from app.services.urls import safe_media_url

PARSER_VERSION = "2"
_POST_PATH = "/app/bbs/link/"
_PROFILE_PATH = "/app/user/profile/"
_WEB_HOSTS = {"xiaoheihe.cn", "www.xiaoheihe.cn"}
_LINK_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,48}$")
_MAX_METRIC = 2_147_483_647


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


def extract_link_id(raw_url: str | None) -> str:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return ""
    url = urlsplit(raw_url.strip())
    if url.scheme != "https":
        return ""
    link_id = ""
    if url.netloc in _WEB_HOSTS and url.path.startswith(_POST_PATH):
        link_id = url.path.removeprefix(_POST_PATH).rstrip("/")
    elif url.netloc == "api.xiaoheihe.cn" and url.path == "/v3/bbs/app/api/web/share":
        ids = parse_qs(url.query).get("link_id", [])
        if len(ids) == 1:
            link_id = ids[0]
    return link_id if _LINK_ID_RE.fullmatch(link_id) else ""


def _parse_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= _MAX_METRIC else None


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _extract_media(raw: dict) -> list[str]:
    urls: list[str] = []
    for key in ("media", "imgs", "thumbs"):
        vals = raw.get(key)
        if not isinstance(vals, list):
            continue
        for entry in vals:
            candidate = entry.get("url") if isinstance(entry, dict) else entry
            safe = safe_media_url(candidate)
            if safe and safe not in urls:
                urls.append(safe)
            if len(urls) >= 9:
                return urls
    return urls


def _extract_metrics(raw: dict) -> dict[str, int]:
    nested = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    out: dict[str, int] = {}
    view = _parse_int(nested.get("view") if "view" in nested else (raw.get("click") if "click" in raw else raw.get("view")))
    reply = _parse_int(nested.get("reply") if "reply" in nested else (raw.get("comment_num") if "comment_num" in raw else raw.get("reply")))
    like = _parse_int(
        nested.get("like")
        if "like" in nested
        else (
            raw.get("link_award_num")
            if raw.get("link_award_num") is not None
            else (
                raw.get("award")
                if raw.get("award") is not None
                else (raw.get("up") if raw.get("up") is not None else raw.get("like"))
            )
        )
    )
    if view is not None:
        out["view"] = view
    if reply is not None:
        out["reply"] = reply
    if like is not None:
        out["like"] = like
    return out


def _extract_author(raw: dict, link_id: str, author: NormalizedAuthor | None = None) -> NormalizedAuthor:
    if author is not None:
        return author
    raw_author = raw.get("_author") if isinstance(raw.get("_author"), dict) else raw.get("author")
    if isinstance(raw_author, dict):
        ext_id = str(raw_author.get("external_id") or raw_author.get("userid") or "").strip() or f"share:{link_id}"
        name = str(raw_author.get("name") or raw_author.get("username") or "").strip()[:64] or None
        return NormalizedAuthor(external_id=ext_id, name=name, identity=Identity.USER)
    uid = str(raw.get("userid") or "").strip()
    name = str(raw.get("username") or (raw_author if isinstance(raw_author, str) else "") or "").strip()[:64] or None
    return NormalizedAuthor(external_id=uid if uid else f"share:{link_id}", name=name, identity=Identity.USER)


def parse_share(raw: dict) -> NormalizedContent:
    """解析后台手工收录或 RSS 分享条目。"""
    link_id = extract_link_id(raw.get("url"))
    if not link_id:
        raise ValueError("请填写小黑盒公开帖子链接")
    text = str(raw.get("text") or raw.get("description") or "").strip()[:4000] or None
    return NormalizedContent(
        platform=Platform.XIAOHEIHE,
        content_type=ContentType.POST,
        external_id=link_id,
        url=f"https://www.xiaoheihe.cn{_POST_PATH}{link_id}",
        title=str(raw["title"]).strip()[:240],
        text=text,
        published_at=_parse_time(raw.get("published_at")),
        media=_extract_media(raw),
        metrics=_extract_metrics(raw),
        # 分享链接只提供作者昵称，引用条目不推断跨帖账号身份。
        author=NormalizedAuthor(
            external_id=f"share:{link_id}",
            name=str(raw.get("author") or "").strip()[:64] or None,
            identity=Identity.USER,
        ),
    )


def parse_post(raw: dict, *, author: NormalizedAuthor | None = None) -> NormalizedContent:
    """统一解析小黑盒原帖（兼容 list_v2 账号动态、只读网关搜索/热帖与分享条目）。"""
    link_id = extract_link_id(raw.get("share_url")) or extract_link_id(raw.get("url"))
    if not link_id:
        for candidate in (raw.get("share_hex"), raw.get("external_id"), raw.get("linkid")):
            val = str(candidate or "").strip()
            if _LINK_ID_RE.fullmatch(val):
                link_id = val
                break
    if not link_id:
        raise ValueError("缺少有效的小黑盒帖子标识")

    description = str(raw.get("text") or raw.get("description") or "").strip()
    raw_title = str(raw.get("title") or "").strip()
    title = (raw_title or (description.splitlines()[0] if description else "")).strip()[:240]
    if not title:
        raise ValueError("小黑盒帖子缺少标题")

    text = description[:4000] or None
    topics_raw = raw.get("topics")
    topics: list[str] = []
    if isinstance(topics_raw, list):
        for t in topics_raw:
            tname = t.get("name") if isinstance(t, dict) else t
            if isinstance(tname, str) and tname.strip():
                topics.append(tname.strip()[:64])

    extra: dict = {}
    if topics:
        extra["topics"] = list(dict.fromkeys(topics))[:10]

    return NormalizedContent(
        platform=Platform.XIAOHEIHE,
        content_type=ContentType.POST,
        external_id=link_id,
        url=f"https://www.xiaoheihe.cn{_POST_PATH}{link_id}",
        title=title,
        text=text,
        published_at=_parse_time(raw.get("published_at") if raw.get("published_at") is not None else raw.get("create_at")),
        media=_extract_media(raw),
        metrics=_extract_metrics(raw),
        author=_extract_author(raw, link_id, author=author),
        extra=extra,
    )


def parse_account_post(raw: dict, author: NormalizedAuthor) -> NormalizedContent | None:
    if str(raw.get("userid")) != author.external_id:
        return None
    try:
        return parse_post(raw, author=author)
    except ValueError:
        return None
