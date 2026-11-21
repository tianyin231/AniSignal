"""小黑盒主题流：能力探测、状态汇总与热帖兼容刷新。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import CollectorError
from app.collectors.xiaoheihe import XiaoheiheCollector
from app.communities import COMMUNITIES
from app.models import CollectTarget, ContentVersion, CustomFeedSource, RawContent
from app.models.enums import Platform
from app.schemas.api import ContentFeedStatus
from app.schemas.content import NormalizedContent
from app.services.collector_proxy import fetch_with_fallback
from app.services.ingest import ingest_items
from app.services.settings import get_effective_xiaoheihe, xiaoheihe_public_view

XIAOHEIHE_KEY = Platform.XIAOHEIHE.value
XIAOHEIHE_KIND = "hot_posts"
XIAOHEIHE_ACCOUNT_KIND = "xhh_account"
XIAOHEIHE_KEYWORD_KIND = "xhh_keyword"
XIAOHEIHE_TARGET = "hot"
XIAOHEIHE_INTERVAL_MINUTES = 120
XIAOHEIHE_LIMIT = 30


async def xiaoheihe_capabilities(session: AsyncSession | None) -> dict:
    return xiaoheihe_public_view(await get_effective_xiaoheihe(session))


async def xiaoheihe_hot_configured(session: AsyncSession | None) -> bool:
    meta = COMMUNITIES.get(XIAOHEIHE_KEY)
    if not (meta and meta.active):
        return False
    return bool((await xiaoheihe_capabilities(session))["hot_enabled"])


async def get_xiaoheihe_source(session: AsyncSession) -> CustomFeedSource | None:
    return await session.scalar(select(CustomFeedSource).where(
        CustomFeedSource.platform == XIAOHEIHE_KEY,
        CustomFeedSource.kind == XIAOHEIHE_KIND,
        CustomFeedSource.target_value == XIAOHEIHE_TARGET,
    ))


async def xiaoheihe_feed_status(session: AsyncSession) -> ContentFeedStatus:
    caps = await xiaoheihe_capabilities(session)
    targets = list((await session.execute(select(
        CollectTarget.last_success_at, CollectTarget.last_error,
    ).where(
        CollectTarget.community == XIAOHEIHE_KEY,
        CollectTarget.enabled.is_(True),
    ))).all())
    source = await get_xiaoheihe_source(session) if caps["hot_enabled"] else None
    automatic = bool(caps["hot_enabled"] or targets)

    candidates = [t[0] for t in targets if t[0] is not None]
    if source and source.last_success_at is not None:
        candidates.append(source.last_success_at)
    updated_at = max(candidates) if candidates else None
    if updated_at is None:
        updated_at = await session.scalar(select(func.max(RawContent.collected_at)).where(
            RawContent.community == XIAOHEIHE_KEY,
            RawContent.platform == XIAOHEIHE_KEY,
            RawContent.hidden.is_(False),
        ))
    stale = bool((source and source.last_error) or (targets and all(t[1] for t in targets)))
    return ContentFeedStatus(automatic=automatic, updated_at=updated_at, stale=stale)


async def refresh_xiaoheihe_items(session: AsyncSession) -> list[NormalizedContent]:
    cfg = await get_effective_xiaoheihe(session)
    if not (cfg["bridge_url"] or cfg["feed_url"]):
        raise CollectorError("未配置小黑盒热帖采集源，请先在管理端站点设置中配置网关或公开 RSS 地址")

    collector = XiaoheiheCollector()
    collector.configure(cfg)
    result = await fetch_with_fallback(
        collector, session, XIAOHEIHE_KEY, lambda: collector.fetch("hot_posts", XIAOHEIHE_TARGET, XIAOHEIHE_LIMIT),
    )
    from app.parsers.xiaoheihe import parse_post

    posts: dict[str, dict] = {}
    for raw in result.items:
        try:
            post = parse_post(raw)
        except ValueError:
            continue
        posts[post.external_id] = raw
    if not posts:
        raise CollectorError("小黑盒热帖源本轮没有返回可用的公开原帖")

    other_partitions = set(await session.scalars(select(RawContent.external_id).where(
        RawContent.platform == XIAOHEIHE_KEY,
        RawContent.community != XIAOHEIHE_KEY,
        RawContent.external_id.in_(posts),
    )))
    batch = [raw for key, raw in posts.items() if key not in other_partitions]
    if batch:
        stats = await ingest_items(session, "xiaoheihe_posts", batch, XIAOHEIHE_KEY)
        if len(stats.errors) == len(batch):
            raise CollectorError("小黑盒热帖源本轮收录失败")

    latest_version = select(func.max(ContentVersion.version)).where(
        ContentVersion.raw_content_id == RawContent.id,
    ).correlate(RawContent).scalar_subquery()
    versions = await session.scalars(select(ContentVersion.normalized_json)
        .join(RawContent, RawContent.id == ContentVersion.raw_content_id)
        .where(RawContent.platform == XIAOHEIHE_KEY, RawContent.community == XIAOHEIHE_KEY,
               RawContent.hidden.is_(False), ContentVersion.version == latest_version)
        .order_by(func.coalesce(RawContent.published_at, RawContent.collected_at).desc(), RawContent.id.desc())
        .limit(XIAOHEIHE_LIMIT))
    return [NormalizedContent.model_validate(version) for version in versions]
