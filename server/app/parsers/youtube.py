"""YouTube RSS item 解析器：collector 已把 XML entry 整理为 dict（原始层）。"""

from datetime import datetime

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "2"


def parse_rss_item(raw: dict) -> NormalizedContent:
    video_id = str(raw.get("id") or "")
    return NormalizedContent(
        platform=Platform.YOUTUBE,
        content_type=ContentType.RSS_ITEM,
        external_id=video_id,
        author=NormalizedAuthor(
            external_id=raw.get("channel_id") or None,
            name=raw.get("author") or None,
            official_title="YouTube 官方频道",
            identity=Identity.OFFICIAL,
        ),
        title=raw.get("title") or None,
        text=raw.get("summary") or None,
        url=raw.get("link") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None),
        published_at=_iso(raw.get("published")),
        media=[raw["thumbnail"]] if raw.get("thumbnail") else [],
        metrics={},  # RSS 无指标；热度追踪后续走 yt-dlp 元数据补充
    )


def _iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
