"""定制监控采集：用户自建的 UID / 关键词抓取源。

与主内容库完全隔离——条目进 custom_feed_items，不进 raw_contents/事件引擎；
新消息推送直接复用投递行 SMTP（send_admin_email），每个监控每天至多推送一次。
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.collectors.base import platform_slot
from app.collectors.bilibili import BilibiliCollector
from app.models import CustomFeed, CustomFeedItem, NotificationDelivery, User
from app.parsers.bilibili import parse_dynamic, parse_video
from app.services.email_templates import notice_email
from app.services.mailer import send_email
from app.services.settings import get_effective_smtp

logger = logging.getLogger(__name__)

SWEEP_BATCH = 20
MAX_FEEDS_PER_USER = 10
PUSH_MAX_ITEMS = 5
CUSTOM_KINDS = {"uid", "keyword"}
CUSTOM_PLATFORMS = {"bilibili"}


def _collector(platform: str):
    if platform == "bilibili":
        return BilibiliCollector()
    raise ValueError(f"unsupported custom platform: {platform}")


async def fetch_custom_items(feed: CustomFeed, redis) -> list:
    """抓取并解析定制源；平台礼貌间隔与主采集共享同一个 Redis 槽。"""
    collector = _collector(feed.platform)
    if feed.kind == "uid":
        async with platform_slot(redis, feed.platform, 5.0):
            raw_items = await collector.user_dynamics(feed.target_value, 20)
        return [n for n in (parse_dynamic(i) if i else None for i in raw_items) if n is not None]
    if feed.kind == "keyword":
        async with platform_slot(redis, feed.platform, 5.0):
            raw_items = await collector.search_videos(feed.target_value, 30)
        return [n for n in (parse_video(i) if i else None for i in raw_items) if n is not None]
    raise ValueError(f"unsupported custom kind: {feed.kind}")


async def collect_custom_feed(session: AsyncSession, feed: CustomFeed, redis) -> int:
    """抓取单个定制源并把新条目落库，返回新增条数。"""
    normalized_list = await fetch_custom_items(feed, redis)
    existing = set(
        (
            await session.execute(
                select(CustomFeedItem.external_id).where(CustomFeedItem.feed_id == feed.id)
            )
        ).scalars()
    )
    new_count = 0
    for n in normalized_list:
        if n.external_id in existing:
            continue
        view = int(n.metrics.get("view") or 0)
        if feed.kind == "keyword" and feed.min_views > 0 and view < feed.min_views:
            continue
        session.add(
            CustomFeedItem(
                feed_id=feed.id,
                platform=n.platform.value,
                content_type=n.content_type.value,
                external_id=n.external_id,
                title=(n.title or n.external_id)[:255],
                url=n.url,
                cover=n.media[0] if n.media else None,
                author_name=(n.author.name if n.author else None),
                view=view,
                published_at=to_naive_utc(n.published_at) if n.published_at else None,
            )
        )
        existing.add(n.external_id)
        new_count += 1
    feed.last_collected_at = utcnow()
    await session.commit()
    return new_count


async def push_custom_feed_digest(session: AsyncSession, feed: CustomFeed, new_count: int) -> bool:
    """新消息推送：每监控每天至多一封，正文列出最新几条标题。"""
    user = await session.get(User, feed.user_id)
    if user is None or user.status != "active" or not user.is_verified:
        return False
    today = utcnow().date()
    dedupe_key = f"custom-push:{feed.id}:{today.isoformat()}"
    existing = await session.scalar(
        select(NotificationDelivery.id).where(NotificationDelivery.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return False

    latest = list(
        (
            await session.execute(
                select(CustomFeedItem)
                .where(CustomFeedItem.feed_id == feed.id)
                .order_by(CustomFeedItem.published_at.desc(), CustomFeedItem.id.desc())
                .limit(PUSH_MAX_ITEMS)
            )
        ).scalars()
    )
    lines = [f"- {item.title or item.external_id}" for item in latest]
    subject = f"定制监控「{feed.name}」有 {new_count} 条新内容"
    body = f"你监控的「{feed.name}」新增 {new_count} 条内容：\n" + "\n".join(lines)
    delivery = NotificationDelivery(
        user_id=feed.user_id,
        kind="custom",
        source="schedule",
        period_date=today,
        dedupe_key=dedupe_key,
        status="pending",
        subject=subject[:255],
        body=body,
    )
    session.add(delivery)
    await session.commit()

    from app.services.notifications import send_admin_email

    result = await send_admin_email(session, delivery.id)
    return result.get("status") == "sent"


async def sweep_custom_feeds(session: AsyncSession, redis, limit: int = SWEEP_BATCH) -> dict:
    """调度入口：抓取所有到期定制源（每轮限量，平台礼貌间隔内自然限速）。"""
    now = utcnow()
    feeds = list(
        (
            await session.execute(
                select(CustomFeed)
                .where(
                    CustomFeed.enabled.is_(True),
                    CustomFeed.status == "active",
                    CustomFeed.platform == "bilibili",
                )
                .order_by(func.coalesce(CustomFeed.next_run_at, CustomFeed.created_at))
                .limit(limit)
            )
        ).scalars()
    )
    stats = {"feeds": 0, "new_items": 0, "pushed": 0, "failed": 0}
    for feed in feeds:
        if feed.next_run_at and feed.next_run_at > now:
            continue
        feed.next_run_at = now + timedelta(minutes=feed.interval_minutes)
        await session.commit()
        try:
            new_count = await collect_custom_feed(session, feed, redis)
            stats["feeds"] += 1
            stats["new_items"] += new_count
            if new_count and feed.push_enabled:
                stats["pushed"] += 1 if await push_custom_feed_digest(session, feed, new_count) else 0
        except Exception as exc:  # noqa: BLE001 - 单源失败不影响其余
            stats["failed"] += 1
            logger.warning("custom feed %s (%s) collect failed: %s", feed.id, feed.target_value, exc)
    if stats["feeds"]:
        logger.info("custom feed sweep: %s", stats)
    return stats


async def count_feed_items(session: AsyncSession, feed_ids: list[int]) -> dict[int, int]:
    if not feed_ids:
        return {}
    rows = await session.execute(
        select(CustomFeedItem.feed_id, func.count())
        .where(CustomFeedItem.feed_id.in_(feed_ids))
        .group_by(CustomFeedItem.feed_id)
    )
    return {fid: cnt for fid, cnt in rows}
