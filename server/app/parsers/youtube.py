"""YouTube RSS item 解析器：collector 已把 XML entry 整理为 dict（原始层）。"""

from datetime import datetime

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "3"

# 兜底白名单；配置 official_youtube_channels 优先（"频道ID:名称" 逗号分隔）
FALLBACK_OFFICIAL_CHANNELS = {"UC0Bi5KMcECRVYis5Gb_ZYZQ": "Wuthering Waves 官方频道"}


def _official_channels() -> dict[str, str]:
    from app.config import get_settings

    mapping: dict[str, str] = {}
    for part in get_settings().official_youtube_channels.split(","):
        part = part.strip()
        if not part:
            continue
        channel_id, _, label = part.partition(":")
        if channel_id:
            mapping[channel_id] = label or "官方频道"
    return mapping or FALLBACK_OFFICIAL_CHANNELS


def parse_rss_item(raw: dict) -> NormalizedContent:
    video_id = str(raw.get("id") or "")
    channel_id = str(raw.get("channel_id") or "")
    official_title = _official_channels().get(channel_id)
    is_official = bool(official_title)
    return NormalizedContent(
        platform=Platform.YOUTUBE,
        content_type=ContentType.RSS_ITEM,
        external_id=video_id,
        author=NormalizedAuthor(
            external_id=channel_id or None,
            name=raw.get("author") or None,
            official_title=official_title,
            identity=Identity.OFFICIAL if is_official else Identity.USER,
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
