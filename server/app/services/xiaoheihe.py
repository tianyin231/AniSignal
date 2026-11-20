"""小黑盒主题流：归一公开原帖，供分区和个人监控共用。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import CollectorError
from app.collectors.rss import RssCollector
from app.communities import COMMUNITIES
from app.config import get_settings
from app.models import ContentVersion, CustomFeedSource, RawContent
from app.models.enums import Platform
from app.parsers.xiaoheihe import parse_share
from app.schemas.api import ContentFeedStatus
from app.schemas.content import NormalizedContent
from app.services.ingest import ingest_items

XIAOHEIHE_KEY = Platform.XIAOHEIHE.value
XIAOHEIHE_KIND = "hot_posts"
XIAOHEIHE_TARGET = "hot"
XIAOHEIHE_INTERVAL_MINUTES = 120
XIAOHEIHE_LIMIT = 30


def xiaoheihe_feed_url() -> str:
    meta = COMMUNITIES.get(XIAOHEIHE_KEY)
    return get_settings().xiaoheihe_feed_url.strip() if meta and meta.active else ""


async def get_xiaoheihe_source(session: AsyncSession) -> CustomFeedSource | None:
    return await session.scalar(select(CustomFeedSource).where(
        CustomFeedSource.platform == XIAOHEIHE_KEY,
        CustomFeedSource.kind == XIAOHEIHE_KIND,
        CustomFeedSource.target_value == XIAOHEIHE_TARGET,
    ))


async def xiaoheihe_feed_status(session: AsyncSession) -> ContentFeedStatus:
    automatic = bool(xiaoheihe_feed_url())
    source = await get_xiaoheihe_source(session) if automatic else None
    updated_at = source.last_success_at if source else None
    if updated_at is None:
        updated_at = await session.scalar(select(func.max(RawContent.collected_at)).where(
            RawContent.community == XIAOHEIHE_KEY,
            RawContent.platform == XIAOHEIHE_KEY,
            RawContent.hidden.is_(False),
        ))
    return ContentFeedStatus(automatic=automatic, updated_at=updated_at, stale=bool(source and source.last_error))


async def refresh_xiaoheihe_items(session: AsyncSession) -> list[NormalizedContent]:
    if url := xiaoheihe_feed_url():
        collector = RssCollector()
        collector.source_config = {"engagement": True}
        result = await collector.fetch("feed", url, XIAOHEIHE_LIMIT)
        posts = {}
        for entry in result.items:
            if not entry["title"]:
                continue
            raw = {
                "url": entry["url"],
                "title": entry["title"][:240],
                "author": entry["author"][:64] or None,
                "published_at": entry.get("published"),
                "like": entry.get("like"),
                "reply": entry.get("reply"),
            }
            try:
                post = parse_share(raw)
            except ValueError:
                continue
            raw["url"] = post.url
            posts[post.external_id] = raw
        if not posts:
            raise CollectorError("小黑盒订阅源本轮没有返回可用的公开原帖")
        other_partitions = set(await session.scalars(select(RawContent.external_id).where(
            RawContent.platform == XIAOHEIHE_KEY,
            RawContent.community != XIAOHEIHE_KEY,
            RawContent.external_id.in_(posts),
        )))
        batch = [raw for key, raw in posts.items() if key not in other_partitions]
        if batch:
            stats = await ingest_items(session, "xiaoheihe_share", batch, XIAOHEIHE_KEY)
            if len(stats.errors) == len(batch):
                raise CollectorError("小黑盒订阅源本轮收录失败")

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
