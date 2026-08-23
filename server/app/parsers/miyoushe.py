"""米游社帖子解析器：collector item = {entry: 搜索项, detail: getPostFull 的 data}。

接口字段存在多种包裹形态（post/user/stat 嵌套或扁平），全部按容错方式取值；
原始 JSON 已入库，字段形态变化由版本机制发现。
"""

from datetime import datetime, timezone

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"


def parse_post(raw: dict) -> NormalizedContent:
    entry = raw.get("entry") or {}
    post = entry.get("post") if isinstance(entry.get("post"), dict) else entry
    user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
    detail = raw.get("detail") or {}
    detail_post = detail.get("post") if isinstance(detail.get("post"), dict) else {}

    post_id = str(post.get("post_id") or detail_post.get("post_id") or "")
    stat = _first_dict(detail_post.get("stat"), post.get("stat"), entry.get("stat"))
    metrics = {
        "view": _int(stat.get("view")),
        "like": _int(stat.get("like")),
        "reply": _int(stat.get("reply")),
        "favorite": _int(stat.get("favorite")),
        "share": _int(stat.get("share")),
    }
    images = _images(detail_post.get("images")) or _images(post.get("images"))
    return NormalizedContent(
        platform=Platform.MIYOUSHE,
        content_type=ContentType.POST,
        external_id=post_id,
        author=NormalizedAuthor(
            external_id=str(user.get("uid")) if user.get("uid") is not None else None,
            name=user.get("nickname"),
        ),
        title=post.get("subject") or detail_post.get("subject") or None,
        text=detail_post.get("content") or post.get("content") or post.get("summary") or None,
        url=f"https://www.miyoushe.com/ys/article/{post_id}" if post_id else None,
        published_at=_created_at(post.get("created_at") or detail_post.get("created_at")),
        media=images,
        metrics=metrics,
        extra={"forum": _first_dict(entry.get("forum")).get("name")},
    )


def _first_dict(*candidates) -> dict:
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def _images(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _created_at(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
