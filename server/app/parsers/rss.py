"""RSS 原始条目转为主系统统一内容；来源身份由管理员配置决定。"""

from app.collectors.rss import feed_time, rss_id
from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"


def parse_item(raw: dict) -> NormalizedContent:
    official = bool(raw.get("first_party"))
    return NormalizedContent(
        platform=Platform.RSS, content_type=ContentType.RSS_ITEM,
        external_id=rss_id(raw["url"]), title=raw.get("title"), text=raw.get("text"),
        url=raw["url"], published_at=feed_time(raw.get("published") or ""),
        author=NormalizedAuthor(external_id=rss_id(raw["feed_url"]), name=raw.get("source_name"),
            identity=Identity.OFFICIAL if official else Identity.MEDIA),
        media=raw.get("media") or [], extra={"author": raw.get("author"), "owner_key": raw.get("owner_key"),
            "feed_url": raw["feed_url"], "participation_mode": raw.get("participation_mode", "editorial"), "tier": raw.get("tier", "T2")},
    )
