"""幂等入库：原始 JSON 先落库、解析产物版本化、指标进快照表。

流程契约（见项目 skill"数据解析层"）：
- 每条原始 item 经 parser 得到 NormalizedContent；
- (platform, external_id) 已存在 → 不覆盖 raw_json，只更新 last_seen；
- 内容指纹变化 → 追加新 ContentVersion（可全量重算）；
- 指标每次采集都记 MetricSnapshot（用于"首次越过阈值"判断）。
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.communities import DEFAULT_COMMUNITY, get_community
from app.models import ContentVersion, MetricSnapshot, RawContent, Source
from app.models.enums import Identity
from app.parsers.bilibili import PARSER_VERSION as BILIBILI_PARSER_VERSION
from app.parsers.bilibili import parse_dynamic, parse_video
from app.parsers.kuro import PARSER_VERSION as KURO_PARSER_VERSION
from app.parsers.kuro import parse_notice
from app.parsers.miyoushe import PARSER_VERSION as MIYOUSHE_PARSER_VERSION
from app.parsers.miyoushe import parse_post
from app.parsers.youtube import PARSER_VERSION as YOUTUBE_PARSER_VERSION
from app.parsers.youtube import parse_rss_item
from app.parsers.x import PARSER_VERSION as X_PARSER_VERSION, parse_post as parse_x_post
from app.parsers.rss import PARSER_VERSION as RSS_PARSER_VERSION, parse_item as parse_feed_item
from app.parsers.game_official import PARSER_VERSION as GAME_OFFICIAL_PARSER_VERSION, parse_notice as parse_game_notice
from app.schemas.content import NormalizedContent
from app.services.classify import classify_content, load_rules
from app.services.fingerprint import content_fingerprint

logger = logging.getLogger(__name__)

# fetch_kind → (解析函数, parser 版本)；新平台接入时在这里注册
PARSERS: dict[str, tuple] = {
    "bilibili_search": (parse_video, BILIBILI_PARSER_VERSION),
    "bilibili_user_videos": (parse_video, BILIBILI_PARSER_VERSION),
    "bilibili_dynamics": (parse_dynamic, BILIBILI_PARSER_VERSION),
    "kuro_notices": (parse_notice, KURO_PARSER_VERSION),
    "miyoushe_posts": (parse_post, MIYOUSHE_PARSER_VERSION),
    "youtube_rss": (parse_rss_item, YOUTUBE_PARSER_VERSION),
    "x_posts": (parse_x_post, X_PARSER_VERSION),
    "rss_feed": (parse_feed_item, RSS_PARSER_VERSION),
    "game_official_notices": (parse_game_notice, GAME_OFFICIAL_PARSER_VERSION),
}


@dataclass
class IngestStats:
    items_found: int = 0
    items_new: int = 0
    items_updated: int = 0  # 已存在且指纹变化（产生新版本）的条数
    parse_failed: int = 0
    official_items: int = 0
    community_items: int = 0
    errors: list[str] = field(default_factory=list)
    touched_content_ids: list[int] = field(default_factory=list)


async def ingest_items(
    session: AsyncSession, fetch_kind: str, items: list[dict], community: str = DEFAULT_COMMUNITY
) -> IngestStats:
    if fetch_kind not in PARSERS:
        raise ValueError(f"no parser registered for fetch_kind={fetch_kind}")
    parse, parser_version = PARSERS[fetch_kind]
    rules = await load_rules(session)
    stats = IngestStats(items_found=len(items))
    for raw in items:
        try:
            normalized = parse(raw)
            if not normalized.external_id:
                raise ValueError("parser produced empty external_id")
        except Exception as exc:  # noqa: BLE001 - 单条解析失败不中断批次
            stats.parse_failed += 1
            stats.errors.append(f"parse failed: {exc!r}")
            logger.warning("parse failed for %s item: %r", fetch_kind, exc)
            continue
        try:
            is_new, changed, content_id, identity = await _ingest_one(
                session, normalized, raw, parser_version, rules, community
            )
            stats.items_new += int(is_new)
            stats.items_updated += int(changed)
            stats.official_items += int(identity == Identity.OFFICIAL)
            stats.community_items += int(identity != Identity.OFFICIAL)
            if is_new or changed:
                stats.touched_content_ids.append(content_id)
        except Exception as exc:  # noqa: BLE001
            stats.errors.append(f"ingest failed: {exc!r}")
            logger.warning(
                "ingest failed for %s/%s: %r",
                normalized.platform,
                normalized.external_id,
                exc,
            )
    await session.commit()
    return stats


def _matched_keywords(raw: dict) -> list[str]:
    return [str(k) for k in (raw.get("_matched_keywords") or []) if k][:8]


async def _ingest_one(
    session: AsyncSession,
    normalized: NormalizedContent,
    raw: dict,
    parser_version: str,
    rules: list,
    community: str = DEFAULT_COMMUNITY,
) -> tuple[bool, bool, int, str]:
    """返回 (is_new, fingerprint_changed, content_id, source_identity)。"""
    from app.services.statistics import record_content_stat

    source_id, identity = await _upsert_source(session, normalized)
    fingerprint = content_fingerprint(normalized)

    existing = await session.scalar(
        select(RawContent).where(
            RawContent.platform == normalized.platform,
            RawContent.external_id == normalized.external_id,
        )
    )
    if existing is None:
        content = RawContent(
            platform=normalized.platform,
            content_type=normalized.content_type,
            external_id=normalized.external_id,
            community=community,
            source_id=source_id,
            url=normalized.url,
            published_at=to_naive_utc(normalized.published_at) if normalized.published_at else None,
            collected_at=utcnow(),
            last_seen_at=utcnow(),
            raw_json=raw,
            content_hash=fingerprint,
            matched_keywords=_matched_keywords(raw),
        )
        session.add(content)
        await session.flush()  # 拿到 id 供 version/snapshot 外键使用
        _add_version(session, content, 1, normalized, parser_version, rules, community)
        _add_snapshot(session, content, normalized)
        await record_content_stat(
            session,
            collected_at=content.collected_at,
            platform=str(content.platform),
            identity=str(identity),
            keywords=list(content.matched_keywords or []),
            include_base=True,
        )
        return True, False, content.id, str(identity)

    # 已存在：原始数据不可覆盖，只更新观测字段
    existing.last_seen_at = utcnow()
    if source_id is not None:
        existing.source_id = source_id
    if normalized.url and not existing.url:
        existing.url = normalized.url
    previous_keywords = set(existing.matched_keywords or [])
    merged = sorted({*previous_keywords, *_matched_keywords(raw)})
    existing.matched_keywords = merged
    added_keywords = sorted(set(merged) - previous_keywords)
    if added_keywords:
        await record_content_stat(
            session,
            collected_at=existing.collected_at,
            platform=str(existing.platform),
            identity=str(identity),
            keywords=added_keywords,
            include_base=False,
        )

    changed = existing.content_hash != fingerprint
    if changed:
        latest = await session.scalar(
            select(ContentVersion)
            .where(ContentVersion.raw_content_id == existing.id)
            .order_by(ContentVersion.version.desc())
            .limit(1)
        )
        if _would_drop_dynamic_content(normalized, latest):
            changed = False
        else:
            _add_version(
                session, existing, (latest.version if latest else 0) + 1,
                normalized, parser_version, rules,
            )
            existing.content_hash = fingerprint
    _add_snapshot(session, existing, normalized)
    return False, changed, existing.id, str(identity)


def _would_drop_dynamic_content(
    normalized: NormalizedContent, latest: ContentVersion | None
) -> bool:
    if normalized.platform == "x" and latest is not None:
        if not normalized.text and not normalized.media:
            return bool(latest.text or (latest.normalized_json or {}).get("media"))
        # 主页摘要不能把已经补全的相同原帖缩回半截。
        previous = latest.normalized_json or {}
        if normalized.extra.get("truncated") and not previous.get("extra", {}).get("truncated"):
            return bool(latest.text and latest.text.startswith(normalized.text or ""))
    # B 站列表仍能返回动态 ID，但详情受限时正文和图片会同时缺失。
    # 保留已经补全的版本；只拦截全空结果，不将历史字段拼进真实的新内容。
    return bool(
        normalized.platform == "bilibili"
        and normalized.content_type == "dynamic"
        and not (normalized.text or "").strip()
        and not normalized.media
        and latest is not None
        and ((latest.text or "").strip() or (latest.normalized_json or {}).get("media"))
    )


async def _upsert_source(
    session: AsyncSession, normalized: NormalizedContent
) -> tuple[int | None, str]:
    author = normalized.author
    if not author or not author.external_id:
        # 账号体系外的官方来源（如库洛官网公告）：用平台级伪 ID 建一条 Source，
        # 保证身份过滤与统计可依赖统一的 sources.identity
        if author and author.name and author.identity == Identity.OFFICIAL:
            author = author.model_copy(update={"external_id": f"official-{normalized.platform}"})
        else:
            return None, str(Identity.UNKNOWN)
    identity = Identity.OFFICIAL if author.official_title else author.identity
    existing = await session.scalar(
        select(Source).where(
            Source.platform == normalized.platform,
            Source.external_id == author.external_id,
        )
    )
    if existing is None:
        source = Source(
            platform=normalized.platform,
            external_id=author.external_id,
            name=author.name,
            official_title=author.official_title,
            follower_count=author.follower_count,
            identity=identity,
            raw_json=author.model_dump(mode="json", exclude={"identity"}),
        )
        if normalized.platform == "rss":
            source.owner_key = normalized.extra.get("owner_key")
            source.editorial_tier = {"T1": "first_party", "T1_5": "near_source"}.get(normalized.extra.get("tier"), "media")
            source.raw_json = {**source.raw_json, "participation_mode": normalized.extra.get("participation_mode", "editorial")}
        session.add(source)
        await session.flush()
        return source.id, str(source.identity)
    existing.last_seen_at = utcnow()
    if author.name:
        existing.name = author.name
    if author.official_title:
        existing.official_title = author.official_title
    if author.follower_count is not None:
        existing.follower_count = author.follower_count
    if identity != Identity.UNKNOWN:
        existing.identity = identity
    return existing.id, str(existing.identity)


def _add_version(
    session: AsyncSession,
    content: RawContent,
    version: int,
    normalized: NormalizedContent,
    parser_version: str,
    rules: list,
    community: str | None = None,
) -> None:
    if get_community(community or content.community).domain == "news":
        # 新内容或正文变化先进入候选池，避免未筛选的搜索结果直接公开。
        content.hidden = True
    session.add(
        ContentVersion(
            raw_content_id=content.id,
            version=version,
            parsed_at=utcnow(),
            title=normalized.title,
            text=normalized.text,
            normalized_json=normalized.model_dump(mode="json"),
            parser_version=parser_version,
            # 分区缺省时按内容自身归属（重算路径）
            category=classify_content(
                normalized, rules, community or getattr(content, "community", None) or DEFAULT_COMMUNITY
            ),
        )
    )


def _add_snapshot(
    session: AsyncSession, content: RawContent, normalized: NormalizedContent
) -> None:
    if not normalized.metrics:
        return
    session.add(
        MetricSnapshot(
            raw_content_id=content.id,
            metrics=normalized.metrics,
            captured_at=utcnow(),
        )
    )


# content_type → 用于重算的解析入口（同一解析函数可能有多个 fetch_kind 别名）
_REPARSE_KIND = {
    "video": "bilibili_search",
    "dynamic": "bilibili_dynamics",
    "notice": "kuro_notices",
    "post": "miyoushe_posts",
    "rss_item": "youtube_rss",
}


async def reparse_contents(
    session: AsyncSession, platform: str, content_type: str | None = None,
    *, content_ids: list[int] | None = None,
) -> dict:
    """用当前 parser + 分类词典重算已入库内容（原始数据不可覆盖、解析可重算）。

    parser 升级或词典调整后调用：指纹或分类变化的内容追加新 ContentVersion，
    指标快照不动。
    """
    query = select(RawContent).where(RawContent.platform == platform)
    if content_type:
        query = query.where(RawContent.content_type == content_type)
    if content_ids is not None:
        query = query.where(RawContent.id.in_(content_ids))
    rows = list((await session.execute(query)).scalars())
    rules = await load_rules(session)
    checked = updated = failed = 0
    for content in rows:
        kind = {"x": "x_posts", "rss": "rss_feed"}.get(content.platform) or _REPARSE_KIND.get(content.content_type)
        if kind is None or not content.raw_json:
            continue
        parse, parser_version = PARSERS[kind]
        try:
            normalized = parse(content.raw_json)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("reparse failed for %s/%s: %r", platform, content.external_id, exc)
            continue
        checked += 1
        latest = await session.scalar(
            select(ContentVersion)
            .where(ContentVersion.raw_content_id == content.id)
            .order_by(ContentVersion.version.desc())
            .limit(1)
        )
        if _would_drop_dynamic_content(normalized, latest):
            continue
        category = classify_content(normalized, rules, content.community)
        fingerprint = content_fingerprint(normalized)
        if (
            fingerprint == content.content_hash
            and latest is not None
            and latest.category == category
        ):
            continue
        _add_version(session, content, ((latest.version if latest else 0) + 1), normalized, parser_version, rules)
        content.content_hash = fingerprint
        updated += 1
    await session.commit()
    return {"checked": checked, "updated": updated, "failed": failed}
