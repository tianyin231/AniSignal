"""库洛官网公告解析器。

实测字段形状（2026-08-22，data-sources.md 端点）：
- 列表项：id（字符串）、tabTitle/tabBanner（多语言 dict，zh-Hans / en）、
  startTimeMs / endTimeMs / operateTimeMs（毫秒时间戳）；
- 详情：noticeId、textContent（HTML 正文）。
"""

from datetime import datetime, timezone

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "2"

# 平台 → tabTitle 首选语言
_LANG = {Platform.KURO_CN: "zh-Hans", Platform.KURO_GLOBAL: "en"}


def parse_notice(raw: dict) -> NormalizedContent:
    entry = raw.get("entry") or {}
    detail = raw.get("detail") or {}
    platform = Platform(raw.get("platform") or Platform.KURO_CN)
    lang = _LANG.get(platform, "zh-Hans")

    notice_id = str(
        entry.get("noticeId") or entry.get("id") or entry.get("notice_id")
        or detail.get("noticeId") or ""
    )
    title = (
        _localized(entry.get("tabTitle"), lang)
        or entry.get("title")
        or detail.get("title")
    )
    content_html = (
        detail.get("textContent")
        or detail.get("content")
        or detail.get("htmlContent")
        or entry.get("content")
    )
    banner = (
        _localized(entry.get("tabBanner"), lang)
        or entry.get("banner")
    )
    return NormalizedContent(
        platform=platform,
        content_type=ContentType.NOTICE,
        external_id=notice_id,
        # 官网公告没有作者账号体系；身份直接判定为官方
        author=NormalizedAuthor(name="Kuro Games", identity=Identity.OFFICIAL),
        title=title,
        text=content_html or None,
        url=raw.get("detail_url") or raw.get("list_url"),
        published_at=_time(entry),
        media=[banner] if banner else [],
        metrics={},
        extra={
            "notice_type": entry.get("noticeType"),
            "has_detail": bool(detail),
        },
    )


def _localized(value, lang: str) -> str | None:
    """tabTitle/tabBanner 是多语言 dict，取首选语言，退回第一个非空值。

    tabBanner 的语言值可能是多尺寸 URL 列表，取第一个字符串元素。
    """
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    candidates = [value.get(lang), *(v for k, v in value.items() if k != lang)]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, str) and item:
                    return item
    return None


def _time(entry: dict) -> datetime | None:
    for key in ("startTimeMs", "operateTimeMs", "endTimeMs", "startTime", "time"):
        value = entry.get(key)
        if not value:
            continue
        try:
            seconds = int(value) / 1000 if key.endswith("Ms") else int(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
    return None
