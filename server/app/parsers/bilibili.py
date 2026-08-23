"""B 站解析器：CLI JSON 字段映射（字段清单见 data-sources.md，实测于 2026-08-13）。"""

from datetime import datetime, timezone

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "3"

# 已知官方账号（身份判定用；账号 UID 由配置注入，此处仅做兜底）
_OFFICIAL_MID = "1955897084"  # 鸣潮官方账号，见 data-sources.md


def parse_video(raw: dict) -> NormalizedContent:
    bvid = str(raw.get("bvid") or raw.get("aid") or "")
    metrics = {
        "view": _int(raw.get("view_count")),
        "danmaku": _int(raw.get("danmaku_count")),
        "reply": _int(raw.get("reply_count")),
        "favorite": _int(raw.get("favorite_count")),
        "coin": _int(raw.get("coin_count")),
        "share": _int(raw.get("share_count")),
        "like": _int(raw.get("like_count")),
    }
    return NormalizedContent(
        platform=Platform.BILIBILI,
        content_type=ContentType.VIDEO,
        external_id=bvid,
        author=_author(raw.get("owner_mid"), raw.get("owner_name")),
        title=raw.get("title"),
        text=raw.get("description") or None,
        url=raw.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else None),
        published_at=_ts(raw.get("pubdate")),
        media=[raw["cover_url"]] if raw.get("cover_url") else [],
        metrics=metrics,
        extra={"tags": raw.get("tags") or [], "duration_seconds": raw.get("duration_seconds")},
    )


# CLI 对部分动态类型（如 DRAW）不展开正文与图片字段（data-sources.md 已知限制），
# 至少用类型标签作为展示标题；完整正文需要后续补充原始接口解析
_DYNAMIC_TYPE_LABELS = {
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏动态",
    "DYNAMIC_TYPE_AV": "视频动态",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
}


def parse_dynamic(raw: dict) -> NormalizedContent:
    dynamic_id = str(raw.get("id") or "")
    author = _author(raw.get("author_mid"), raw.get("author_name"))
    if author.external_id == _OFFICIAL_MID:
        author.identity = Identity.OFFICIAL
    metrics = {
        "like": _int(raw.get("stat_like")),
        "reply": _int(raw.get("stat_reply")),
        "forward": _int(raw.get("stat_forward")),
    }
    # 已知限制：部分图文/文章动态 text 为空（data-sources.md），不能假设正文已展开
    text = raw.get("text") or None
    return NormalizedContent(
        platform=Platform.BILIBILI,
        content_type=ContentType.DYNAMIC,
        external_id=dynamic_id,
        author=author,
        # 动态没有标题字段：正文推导 → 类型标签 → 兜底
        title=(
            _display_title(text)
            or _DYNAMIC_TYPE_LABELS.get(raw.get("type") or "")
            or ("图文动态" if raw.get("pics") else "动态")
        ),
        text=text,
        url=f"https://t.bilibili.com/{dynamic_id}" if dynamic_id else None,
        published_at=_ts(raw.get("pub_ts")),
        media=[p for p in (raw.get("pics") or []) if isinstance(p, str)],
        metrics=metrics,
        extra={
            "dynamic_type": raw.get("type"),
            "video_bvid": raw.get("video_bvid") or None,
        },
    )


def _display_title(text: str | None) -> str | None:
    """取正文中第一行有意义的内容作为动态标题（跳过空行与 #话题# 行）。"""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:60] + ("…" if len(stripped) > 60 else "")
    return None


def _author(mid, name) -> NormalizedAuthor:
    identity = Identity.OFFICIAL if str(mid) == _OFFICIAL_MID else Identity.UNKNOWN
    return NormalizedAuthor(
        external_id=str(mid) if mid is not None else None,
        name=name or None,
        identity=identity,
    )


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
