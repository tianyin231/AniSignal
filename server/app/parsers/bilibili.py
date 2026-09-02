"""B 站解析器：CLI JSON 字段映射（字段清单见 data-sources.md，实测于 2026-08-13）。"""

import re
from datetime import datetime, timezone

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent

PARSER_VERSION = "4"

# 已知官方账号（身份判定用；账号 UID 由配置注入，此处仅做兜底）
_OFFICIAL_MIDS = {"1955897084", "233114659"}  # 鸣潮官方 / 碧蓝航线官方，见 data-sources.md


def _official_mids() -> set[str]:
    """官方账号 UID 集合：配置优先（鸣潮/碧蓝航线），空值跳过。"""
    from app.config import get_settings

    settings = get_settings()
    configured = {settings.wuthering_waves_bili_mid, settings.azur_lane_bili_mid}
    known = {mid for mid in configured if mid} or _OFFICIAL_MIDS
    return {str(mid) for mid in known}


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


# CLI 对部分动态类型（如 DRAW）不展开正文与图片字段；采集器会用匿名公开详情补全，
# 若平台临时拒绝详情请求，仍用类型标签作为可追溯的展示标题。
# 自愈：动态采集入库后 _backfill_empty_draw_dynamics 会重试近 7 天的空 DRAW 动态
# （collect.py，每轮最多 5 条；B 站风控瞬时拒绝的场景可在后续轮次补回）。
_DYNAMIC_TYPE_LABELS = {
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏动态",
    "DYNAMIC_TYPE_AV": "视频动态",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
}


def parse_dynamic(raw: dict) -> NormalizedContent:
    dynamic_id = str(raw.get("id") or "")
    author = _author(raw.get("author_mid"), raw.get("author_name"))
    if author.external_id in _official_mids():
        author.identity = Identity.OFFICIAL
    metrics = {
        "like": _int(raw.get("stat_like")),
        "reply": _int(raw.get("stat_reply")),
        "forward": _int(raw.get("stat_forward")),
    }
    # 公开详情也可能只含图片而无描述，不能假设正文一定存在。
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
    """取正文中第一行有意义的内容作为动态标题，移除行首话题标签。"""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        title = re.sub(r"^(?:#[^#\r\n]+#\s*)+", "", stripped).strip()
        if title:
            return title[:60] + ("…" if len(title) > 60 else "")
    return None


def _author(mid, name) -> NormalizedAuthor:
    identity = Identity.OFFICIAL if str(mid) in _official_mids() else Identity.UNKNOWN
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
