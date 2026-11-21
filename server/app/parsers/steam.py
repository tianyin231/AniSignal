"""Steam 官方社区公告解析器（纯匿名官方接口 ISteamNews/GetNewsForApp/v2）。"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import bleach

from app.models.enums import Identity, Platform
from app.schemas.content import ContentType, NormalizedAuthor, NormalizedContent
from app.services.urls import safe_media_url

PARSER_VERSION = "1"
_CLAN_IMAGE_BASE = "https://clan.fastly.steamstatic.com/images"
_STORE_APP_PATH_RE = re.compile(r"^/app/(\d{1,10})(?:/|$)")
_STEAM_STORE_HOSTS = {"store.steampowered.com", "steamcommunity.com"}
_BBCODE_IMG_RE = re.compile(r"\[img\](.*?)\[/img\]", re.IGNORECASE | re.DOTALL)
_HTML_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
_BBCODE_URL_RE = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.IGNORECASE | re.DOTALL)
_BBCODE_HEADING_RE = re.compile(r"\[h[1-6]\](.*?)\[/h[1-6]\]", re.IGNORECASE | re.DOTALL)
_BBCODE_TAG_RE = re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9_*]*(?:=[^\]]*)?\]")
_HTML_BLOCK_BREAK_RE = re.compile(r"(?:<br\s*/?>|</(?:p|div|li|h[1-6]|tr)>)", re.IGNORECASE)
_HTML_DROP_BLOCK_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.IGNORECASE | re.DOTALL)


def extract_app_id(value: str) -> str:
    """接受纯数字 AppID 或 Steam 商店/社区应用链接，返回规范化的数字字符串。"""
    text = str(value or "").strip()
    if "://" in text:
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in _STEAM_STORE_HOSTS:
            raise ValueError("请填写 Steam 数字 AppID 或官方商店链接（store.steampowered.com/app/...）")
        match = _STORE_APP_PATH_RE.match(parsed.path)
        if not match:
            raise ValueError("请填写包含 /app/<AppID> 的 Steam 商店链接")
        text = match.group(1)
    if not re.fullmatch(r"\d{1,10}", text):
        raise ValueError("Steam AppID 必须是 1 至 10 位正整数")
    app_id = int(text)
    if not 1 <= app_id <= 2_147_483_647:
        raise ValueError("Steam AppID 超出有效范围")
    return str(app_id)


def store_url(app_id: int | str) -> str:
    return f"https://store.steampowered.com/app/{app_id}/"


def _expand_clan_images(text: str) -> str:
    return text.replace("{STEAM_CLAN_IMAGE}", _CLAN_IMAGE_BASE).replace("{STEAM_CLAN_LOC_IMAGE}", _CLAN_IMAGE_BASE)


def clean_steam_contents(raw_contents: str) -> tuple[str | None, list[str]]:
    """清洗 Steam 公告正文（BBCode / HTML 混排），同步提取有效配图链接。"""
    if not raw_contents or not raw_contents.strip():
        return None, []
    expanded = _expand_clan_images(raw_contents)
    media: list[str] = []
    for candidate in (*_BBCODE_IMG_RE.findall(expanded), *_HTML_IMG_RE.findall(expanded)):
        url = safe_media_url(html.unescape(candidate).strip())
        if url and url not in media:
            media.append(url)
        if len(media) >= 12:
            break

    text = _BBCODE_IMG_RE.sub("", expanded)
    text = _BBCODE_HEADING_RE.sub(lambda m: f"\n{m.group(1).strip()}\n", text)
    text = _BBCODE_URL_RE.sub(lambda m: m.group(2).strip() or m.group(1).strip(), text)
    text = text.replace("[*]", "\n• ")
    text = _BBCODE_TAG_RE.sub("", text)
    text = _HTML_DROP_BLOCK_RE.sub("", text)
    text = _HTML_BLOCK_BREAK_RE.sub("\n", text)
    text = html.unescape(bleach.clean(text, tags=[], strip=True))
    lines = [line.strip() for line in text.splitlines()]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return (cleaned[:8000] or None), media


def parse_notice(raw: dict) -> NormalizedContent:
    """将 ISteamNews/GetNewsForApp/v2 单条官方公告映射为 NormalizedContent。"""
    item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
    app_id = int(extract_app_id(str(raw.get("app_id") or item.get("appid") or "")))
    gid = str(item.get("gid") or "").strip()
    if not re.fullmatch(r"\d{1,32}", gid):
        raise ValueError("Steam 公告缺少有效 gid")
    title = str(item.get("title") or "").strip()[:240]
    if not title:
        raise ValueError("Steam 公告缺少标题")

    text, media = clean_steam_contents(str(item.get("contents") or ""))
    published_at: datetime | None = None
    raw_date = item.get("date")
    if raw_date is not None:
        try:
            published_at = datetime.fromtimestamp(int(raw_date), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            published_at = None

    app_name = str(raw.get("app_name") or "").strip()[:80]
    author_name = f"{app_name} · Steam" if app_name else f"Steam #{app_id}"
    return NormalizedContent(
        platform=Platform.STEAM,
        content_type=ContentType.NOTICE,
        external_id=f"{app_id}:{gid}",
        title=title,
        text=text,
        url=f"https://store.steampowered.com/news/app/{app_id}/view/{gid}",
        published_at=published_at,
        author=NormalizedAuthor(
            external_id=str(app_id),
            name=author_name,
            official_title="Steam 官方公告",
            identity=Identity.OFFICIAL,
        ),
        media=media,
        metrics={},
        extra={
            "app_id": app_id,
            "feedname": str(item.get("feedname") or "steam_community_announcements"),
            "region": "global",
        },
    )
