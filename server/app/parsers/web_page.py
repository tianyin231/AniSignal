"""自定义网页监控原始条目转为主系统统一内容。"""

from app.collectors.rss import feed_time
from app.collectors.web_page import web_id
from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "1"


def parse_page(raw: dict) -> NormalizedContent:
    source_url = raw.get("source_url") or raw["url"]
    source_ext_id = raw.get("source_id") or web_id(f"site:{source_url}")
    official = bool(raw.get("is_official"))
    return NormalizedContent(
        platform=Platform.WEB_PAGE,
        content_type=ContentType.WEB_PAGE,
        external_id=str(raw.get("id") or web_id(raw["url"])),
        title=raw.get("title"),
        text=raw.get("text"),
        url=raw.get("url"),
        published_at=feed_time(raw.get("published") or ""),
        author=NormalizedAuthor(
            external_id=source_ext_id,
            name=raw.get("source_name"),
            identity=Identity.OFFICIAL if official else Identity.MEDIA,
        ),
        media=raw.get("media") or [],
        extra={
            "watch_mode": raw.get("watch_mode") or "page_watch",
            "source_url": source_url,
            "extractor": raw.get("extractor"),
            "selector": raw.get("selector") or "",
            "rule_sig": raw.get("rule_sig") or "default",
            "text_hash": raw.get("text_hash"),
            "page_title": raw.get("page_title") or raw.get("title"),
            "author": raw.get("author"),
            "spa_warning": bool(raw.get("spa_warning")),
            "body_extracted": bool(raw.get("body_extracted")),
            "change_label": raw.get("change_label"),
            "change_lines": raw.get("change_lines") or [],
        },
    )
