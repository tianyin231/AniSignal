"""RSS 输出层：feed 只读令牌、条目装载与 RSS 2.0 渲染。

借鉴 Inoreader「过滤结果再输出 RSS」：监控任务（采集目标）与归并事件的筛选结果
各自成一份只读订阅源，URL 带 token，可单独撤销/重建。RSSHub（AGPL）只提供设计
思路，这里全部自写，不引入其代码。

条目来源：
- target feed：该目标采集命中的内容（raw_contents.matched_keywords 命中目标词/共现词），
  平台与目标一致，隐藏内容与被打标 ignore 的内容不出现在订阅源里；
- event feed：事件的证据内容（evidences，排除 excluded），事件结论可能被修订，
  因此 feed 只呈现证据条目本身，标题/链接/时间/来源都以原始内容为准。
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import get_settings
from app.models import (
    FEED_KIND_EVENT,
    FEED_KIND_TARGET,
    CollectTarget,
    ContentVersion,
    Event,
    Evidence,
    FeedToken,
    RawContent,
    Source,
)

logger = logging.getLogger(__name__)

# token 长度：token_urlsafe(32) → 43 字符，URL 安全，可安全放在查询串里
TOKEN_BYTES = 32

# 单个 feed 默认/最大条目数
DEFAULT_FEED_LIMIT = 50
MAX_FEED_LIMIT = 100

# 目标 feed 的扫描窗口：只扫最近的内容再按关键词过滤（与 admin 内容列表同一思路）
TARGET_SCAN_LIMIT = 1000

# last_used_at 的最小刷新间隔（避免每次轮询都写库）
LAST_USED_REFRESH = timedelta(minutes=10)

_EXCERPT_LEN = 200


def new_feed_token() -> str:
    """生成只读 token（撤销/重建都走这里，保证随机性与长度一致）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def _xml_text(value) -> str:
    return escape(str(value if value is not None else ""))


def _xml_attr(value) -> str:
    return escape(str(value if value is not None else ""), {'"': "&quot;"})


def _rfc822(value: datetime | None) -> str:
    moment = value or utcnow()
    return format_datetime(moment.replace(tzinfo=timezone.utc))


# ---------- 令牌管理 ----------


async def get_feed_token(
    session: AsyncSession, kind: str, ref_id: int
) -> FeedToken | None:
    if kind not in (FEED_KIND_TARGET, FEED_KIND_EVENT):
        return None
    return await session.scalar(
        select(FeedToken).where(FeedToken.kind == kind, FeedToken.ref_id == ref_id)
    )


async def ensure_feed_token(
    session: AsyncSession, kind: str, ref_id: int
) -> FeedToken:
    """取令牌，没有就自动生成：每个监控任务/事件都"自带"一份只读 feed。"""
    row = await get_feed_token(session, kind, ref_id)
    if row is not None:
        return row
    row = FeedToken(kind=kind, ref_id=ref_id, token=new_feed_token(), status="active")
    session.add(row)
    try:
        await session.commit()
    except Exception:  # noqa: BLE001 - 并发下重复插入只影响一条，重读即可
        await session.rollback()
        existing = await get_feed_token(session, kind, ref_id)
        if existing is not None:
            return existing
        raise
    await session.refresh(row)
    return row


async def ensure_feed_tokens(
    session: AsyncSession, refs: list[tuple[str, int]]
) -> dict[tuple[str, int], FeedToken]:
    """批量确保令牌存在（管理端列表页一次补齐，避免逐条查询）。"""
    result: dict[tuple[str, int], FeedToken] = {}
    pending: list[tuple[str, int]] = []
    for kind, ref_id in refs:
        if (kind, ref_id) in result:
            continue
        row = await get_feed_token(session, kind, ref_id)
        if row is None:
            pending.append((kind, ref_id))
        else:
            result[(kind, ref_id)] = row
    for kind, ref_id in pending:
        result[(kind, ref_id)] = await ensure_feed_token(session, kind, ref_id)
    return result


async def rotate_feed_token(
    session: AsyncSession, kind: str, ref_id: int
) -> FeedToken:
    """重建令牌：换新 token，旧地址立即失效；撤销过的 feed 重建后自动恢复。"""
    row = await ensure_feed_token(session, kind, ref_id)
    row.token = new_feed_token()
    row.status = "active"
    row.revoked_at = None
    row.generation = (row.generation or 0) + 1
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    logger.info("feed token rotated: %s/%s generation=%s", kind, ref_id, row.generation)
    return row


async def revoke_feed_token(
    session: AsyncSession, kind: str, ref_id: int
) -> FeedToken:
    """撤销令牌：保留记录（可见、可重建），但链接立刻不可读。"""
    row = await ensure_feed_token(session, kind, ref_id)
    row.status = "revoked"
    row.revoked_at = utcnow()
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    logger.info("feed token revoked: %s/%s", kind, ref_id)
    return row


async def resolve_feed_token(
    session: AsyncSession, kind: str, ref_id: int, token: str
) -> FeedToken | None:
    """校验只读链接：token 缺失/错误/已撤销都返回 None（不泄露 feed 是否存在）。"""
    if kind not in (FEED_KIND_TARGET, FEED_KIND_EVENT) or not token:
        return None
    row = await get_feed_token(session, kind, ref_id)
    if row is None or row.status != "active":
        return None
    if not secrets.compare_digest(row.token, token):
        return None
    await _touch(session, row)
    return row


async def _touch(session: AsyncSession, row: FeedToken) -> None:
    """记录最近使用时间（限流写入：订阅源被轮询时不必每次落库）。"""
    now = utcnow()
    last = row.last_used_at
    if last is not None and now - last < LAST_USED_REFRESH:
        return
    row.last_used_at = now
    await session.commit()


def feed_url(base: str, kind: str, ref_id: int, token: str) -> str:
    return f"{base.rstrip('/')}/api/feeds/{kind}/{ref_id}?token={token}"


def resolve_base_url(request) -> str:
    """对外基址：优先配置的 public_base_url，否则回落请求基址。"""
    configured = get_settings().public_base_url.strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


# ---------- 条目装载 ----------


async def _latest_versions(
    session: AsyncSession, content_ids: list[int]
) -> dict[int, ContentVersion]:
    if not content_ids:
        return {}
    rows = await session.execute(
        select(ContentVersion)
        .where(ContentVersion.raw_content_id.in_(content_ids))
        .order_by(ContentVersion.raw_content_id, ContentVersion.version.desc())
    )
    latest: dict[int, ContentVersion] = {}
    for version in rows.scalars():
        latest.setdefault(version.raw_content_id, version)
    return latest


def _excerpt(text: str | None) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if "<" in value and ">" in value:
        import bleach

        value = bleach.clean(value, tags=[], strip=True).strip()
    value = " ".join(value.split())
    return value[:_EXCERPT_LEN] + ("…" if len(value) > _EXCERPT_LEN else "")


def _entry(
    raw: RawContent,
    version: ContentVersion | None,
    source_name: str | None,
    base: str,
) -> dict:
    title = (version.title if version else None) or raw.external_id
    link = raw.url or f"{base.rstrip('/')}/contents/{raw.id}"
    published = raw.published_at or raw.collected_at
    return {
        "title": title,
        "link": link,
        "description": _excerpt(version.text if version else None),
        "source": source_name or raw.platform,
        "published_at": published,
        "guid": f"anisignal-content-{raw.id}",
        "cover": None,
    }


def target_terms(target) -> set[str]:
    """目标的关键词集合：目标值、显示名与共现词（与内容分级同一套口径）。"""
    terms: set[str] = set()
    if target.target_value:
        terms.add(str(target.target_value))
    if target.display_name:
        terms.add(str(target.display_name))
    for term in target.co_terms or []:
        if term:
            terms.add(str(term))
    return terms


async def target_entries(
    session: AsyncSession, target, limit: int, base: str
) -> list[dict]:
    """监控任务的最新命中内容（按发布时间倒序）。"""
    terms = target_terms(target)
    if not terms:
        return []
    rows = await session.execute(
        select(RawContent, Source.name)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(
            RawContent.platform == target.platform,
            RawContent.hidden.is_(False),
            or_(RawContent.intent_tier.is_(None), RawContent.intent_tier != "ignore"),
        )
        .order_by(
            func.coalesce(RawContent.published_at, RawContent.collected_at).desc(),
            RawContent.id.desc(),
        )
        .limit(TARGET_SCAN_LIMIT)
    )
    chosen: list[tuple[RawContent, str | None]] = []
    for raw, source_name in rows:
        keywords = {str(item) for item in (raw.matched_keywords or [])}
        if keywords & terms:
            chosen.append((raw, source_name))
        if len(chosen) >= limit:
            break
    versions = await _latest_versions(session, [raw.id for raw, _ in chosen])
    return [
        _entry(raw, versions.get(raw.id), source_name, base)
        for raw, source_name in chosen
    ]


async def event_entries(
    session: AsyncSession, event_id: int, limit: int, base: str
) -> list[dict]:
    """事件的证据内容（排除人工排除的证据），按发布时间倒序。"""
    rows = await session.execute(
        select(RawContent, Source.name)
        .join(Evidence, Evidence.raw_content_id == RawContent.id)
        .join(Source, RawContent.source_id == Source.id, isouter=True)
        .where(
            Evidence.event_id == event_id,
            Evidence.role != "excluded",
            RawContent.hidden.is_(False),
        )
        .order_by(
            func.coalesce(RawContent.published_at, RawContent.collected_at).desc(),
            RawContent.id.desc(),
        )
        .limit(limit)
    )
    chosen = [(raw, source_name) for raw, source_name in rows]
    versions = await _latest_versions(session, [raw.id for raw, _ in chosen])
    return [
        _entry(raw, versions.get(raw.id), source_name, base)
        for raw, source_name in chosen
    ]


async def feed_channel(session: AsyncSession, kind: str, ref_id: int, base: str) -> dict | None:
    """feed 的频道元信息；目标/事件不存在时返回 None。"""
    if kind == FEED_KIND_TARGET:
        target = await session.get(CollectTarget, ref_id)
        if target is None:
            return None
        label = target.display_name or target.target_value
        return {
            "title": f"AniSignal · {label}",
            "link": f"{base.rstrip('/')}/contents",
            "description": f"监控任务「{label}」的最新命中（{target.platform}）",
        }
    event = await session.get(Event, ref_id)
    if event is None:
        return None
    return {
        "title": f"AniSignal · {event.title}",
        "link": f"{base.rstrip('/')}/events/{event.id}",
        "description": event.summary or event.conclusion or "事件证据更新",
    }


# ---------- RSS 2.0 渲染 ----------


def render_rss(channel: dict, entries: list[dict]) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{_xml_text(channel['title'])}</title>",
        f"<link>{_xml_text(channel['link'])}</link>",
        f"<description>{_xml_text(channel.get('description') or '')}</description>",
        "<generator>AniSignal</generator>",
        f"<lastBuildDate>{_rfc822(utcnow())}</lastBuildDate>",
    ]
    for entry in entries:
        parts.extend(
            [
                "<item>",
                f"<title>{_xml_text(entry['title'])}</title>",
                f"<link>{_xml_attr(entry['link'])}</link>",
                f"<guid isPermaLink=\"false\">{_xml_text(entry['guid'])}</guid>",
                f"<pubDate>{_rfc822(entry['published_at'])}</pubDate>",
                f"<description>{_xml_text(entry.get('description') or '')}</description>",
                f"<source>{_xml_text(entry.get('source') or '')}</source>",
            ]
        )
        parts.append("</item>")
    parts.extend(["</channel>", "</rss>"])
    return "\n".join(parts)


async def feed_document(
    session: AsyncSession,
    kind: str,
    ref_id: int,
    token: str,
    base: str,
    limit: int = DEFAULT_FEED_LIMIT,
) -> str | None:
    """校验 token → 装载条目 → 渲染 RSS；token 无效或 feed 不存在返回 None。"""
    if await resolve_feed_token(session, kind, ref_id, token) is None:
        return None
    channel = await feed_channel(session, kind, ref_id, base)
    if channel is None:
        return None
    if kind == FEED_KIND_TARGET:
        target = await session.get(CollectTarget, ref_id)
        entries = await target_entries(session, target, limit, base) if target else []
    else:
        entries = await event_entries(session, ref_id, limit, base)
    return render_rss(channel, entries)
