"""将站点维护的定制信息源接入现有通知，不再次采集或进入公共事件库。"""

from sqlalchemy import and_, select

from app.custom_presets import CUSTOM_PRESETS
from app.models import CustomFeed, CustomFeedItem, CustomSourceItem


def public_feed_query():
    return select(CustomFeed).where(CustomFeed.user_id.is_(None), CustomFeed.is_public.is_(True),
        CustomFeed.enabled.is_(True), CustomFeed.status == "active")


async def candidates(session, target, keywords, start_at, end_at, *, collected_after=None):
    result = []
    for key in target.config.get("custom_feeds", []):
        preset = CUSTOM_PRESETS[key]
        feeds = public_feed_query().where(CustomFeed.platform == preset["platform"],
            CustomFeed.kind == preset["kind"], CustomFeed.target_value == preset["target_value"])
        for feed in await session.scalars(feeds):
            query = select(CustomFeedItem, CustomSourceItem.normalized).outerjoin(CustomSourceItem, and_(
                CustomSourceItem.source_id == feed.source_id,
                CustomSourceItem.external_id == CustomFeedItem.external_id)).where(
                CustomFeedItem.feed_id == feed.id, CustomFeedItem.published_at >= start_at,
                CustomFeedItem.published_at <= end_at)
            if collected_after:
                query = query.where(CustomFeedItem.collected_at > collected_after)
            for item, normalized in (await session.execute(query.order_by(CustomFeedItem.published_at.desc()).limit(100))).all():
                text = (normalized or {}).get("text") or ""
                if keywords and not any(term.casefold() in f"{item.title} {text}".casefold() for term in keywords):
                    continue
                result.append({"id": f"custom:{feed.source_id}:{item.external_id}",
                    "custom_feed": {"preset": key, "feed_id": feed.id},
                    "title": item.title or "NIKKE 国际服官方动态", "text": text,
                    "source": preset["name"], "identity": "official", "community": "",
                    "platform": item.platform, "content_type": item.content_type,
                    "url": item.url, "image": item.cover, "published_at": item.published_at,
                    "collected_at": item.collected_at, "strength": "strong", "importance": False})
    return result


def matches_scope(target, item):
    return item["custom_feed"]["preset"] in target.config.get("custom_feeds", [])


async def valid_item(session, item):
    info = item["custom_feed"]
    preset = CUSTOM_PRESETS.get(info["preset"])
    if not preset:
        return False
    return await session.scalar(public_feed_query().where(CustomFeed.id == info["feed_id"],
        CustomFeed.platform == preset["platform"], CustomFeed.kind == preset["kind"],
        CustomFeed.target_value == preset["target_value"])) is not None
