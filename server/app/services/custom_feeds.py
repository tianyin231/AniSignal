"""定制监控采集：用户自建的 UID / 关键词抓取源。

与主内容库完全隔离——条目进 custom_feed_items，不进 raw_contents/事件引擎；
新消息推送直接复用投递行 SMTP（send_admin_email），每个监控每天至多推送一次。
"""

import logging
from datetime import timedelta

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_naive_utc, utcnow
from app.collectors.base import CollectorDeferred, platform_slot
from app.collectors.bilibili import BilibiliCollector
from app.models import CustomFeed, CustomFeedItem, CustomFeedSource, CustomSourceItem, CustomCollectRun, NotificationDelivery, User
from app.parsers.bilibili import parse_dynamic, parse_video
from app.services.notifications import beijing_today
from app.services.collector_proxy import fetch_with_fallback
from app.services.custom_policy import claim_collection_budget, shared_source
from app.schemas.content import NormalizedContent

logger = logging.getLogger(__name__)

SWEEP_BATCH = 20
PUSH_MAX_ITEMS = 5
CUSTOM_KINDS = {"uid", "keyword"}
CUSTOM_PLATFORMS = {"bilibili"}


def _collector(platform: str):
    if platform == "bilibili":
        return BilibiliCollector()
    raise ValueError(f"unsupported custom platform: {platform}")


async def fetch_custom_items(source: CustomFeedSource, redis, session=None) -> list:
    """抓取并解析定制源；平台礼貌间隔与主采集共享同一个 Redis 槽。"""
    collector = _collector(source.platform)
    if source.kind not in CUSTOM_KINDS:
        raise ValueError(f"unsupported custom kind: {source.kind}")
    async def fetch():
        async with platform_slot(redis, source.platform, 5.0):
            if source.kind == "uid":
                return await collector.user_dynamics(source.target_value, 20)
            return await collector.search_videos(source.target_value, 30)
    raw_items = await fetch_with_fallback(collector, session, source.platform, fetch)
    parse = parse_dynamic if source.kind == "uid" else parse_video
    return [n for n in (parse(i) if i else None for i in raw_items) if n is not None]


async def distribute_custom_feed(session: AsyncSession, feed: CustomFeed) -> int:
    """从共享公开缓存按个人条件分发；不再次访问平台，不进入主公共内容库。"""
    rows = await session.scalars(select(CustomSourceItem).where(CustomSourceItem.source_id == feed.source_id,
        ~exists(select(CustomFeedItem.id).where(CustomFeedItem.feed_id == feed.id,
            CustomFeedItem.external_id == CustomSourceItem.external_id))))
    normalized_list = [NormalizedContent.model_validate(row.normalized) for row in rows]
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
        text = f"{n.title or ''}\n{n.text or ''}".casefold()
        filters = feed.filters or {}
        if filters.get("include") and not any(term.casefold() in text for term in filters["include"]):
            continue
        if any(term.casefold() in text for term in filters.get("exclude", [])):
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
    feed.next_run_at = feed.last_collected_at + timedelta(minutes=feed.interval_minutes)
    await session.commit()
    return new_count


async def push_custom_feed_digest(session: AsyncSession, feed: CustomFeed, new_count: int) -> bool:
    """新消息推送：每监控每天至多一封，正文列出最新几条标题。"""
    user = await session.get(User, feed.user_id, populate_existing=True)
    if user is None or user.status != "active" or not user.is_verified or not user.notification_enabled:
        return False
    today = beijing_today(utcnow())
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
    from app.services.notification_events import custom_feed_notice
    notice = custom_feed_notice(feed, latest, new_count, today)
    delivery = NotificationDelivery(
        user_id=feed.user_id,
        kind="custom",
        source="schedule",
        period_date=today,
        dedupe_key=notice.dedupe_key,
        status="pending",
        subject=notice.subject,
        body=notice.body,
        # custom 类型下记录的是独立监控条目 ID，供投递记录显示真实条数。
        content_ids=notice.content_ids,
    )
    session.add(delivery)
    await session.commit()

    from app.services.notifications import send_admin_email

    result = await send_admin_email(session, delivery.id)
    return result.get("status") == "sent"


def _active_feeds():
    return select(CustomFeed).join(User, User.id == CustomFeed.user_id).where(
        CustomFeed.enabled.is_(True), CustomFeed.status == "active", CustomFeed.platform == "bilibili",
        User.status == "active", User.is_verified.is_(True))


async def _run_shared_source(session, redis, source_id, stats):
    now = utcnow()
    # 原子租约覆盖采集和个人分发；多 worker / 手工触发也不会同时抓同一目标。
    claimed = await session.execute(update(CustomFeedSource).where(CustomFeedSource.id == source_id,
        or_(CustomFeedSource.lease_until.is_(None), CustomFeedSource.lease_until < now))
        .values(lease_until=now + timedelta(minutes=31)).execution_options(synchronize_session=False))
    await session.commit()
    if not claimed.rowcount:
        stats["deferred"] += 1
        return
    run_id = None
    interval = 30
    try:
        source = await session.get(CustomFeedSource, source_id, populate_existing=True)
        feeds = list(await session.scalars(_active_feeds().where(CustomFeed.source_id == source_id).execution_options(populate_existing=True)))
        if not feeds:
            return
        interval = min(f.interval_minutes for f in feeds)
        if source.next_run_at is None or source.next_run_at <= now:
            await claim_collection_budget(session, source.platform, custom=True)
            run = CustomCollectRun(source_id=source_id)
            session.add(run); await session.commit(); run_id = run.id
            stats["sources"] += 1
            items = await fetch_custom_items(source, redis, session)
            old = {row.external_id: row for row in await session.scalars(select(CustomSourceItem).where(
                CustomSourceItem.source_id == source_id, CustomSourceItem.external_id.in_([n.external_id for n in items])))}
            for n in items:
                if n.external_id in old:
                    old[n.external_id].normalized = n.model_dump(mode="json")
                    old[n.external_id].collected_at = utcnow()
                else:
                    row = CustomSourceItem(source_id=source_id, external_id=n.external_id, normalized=n.model_dump(mode="json"))
                    session.add(row); old[n.external_id] = row
            await session.execute(delete(CustomSourceItem).where(CustomSourceItem.source_id == source_id,
                CustomSourceItem.collected_at < now - timedelta(days=30)))
            source.last_success_at = utcnow(); source.consecutive_failures = 0; source.last_error = None
            source.next_run_at = source.last_success_at + timedelta(minutes=interval)
            run.items_found = len(items)
            await session.commit()
        elif source.last_success_at is None or source.consecutive_failures or source.last_error:
            stats["deferred"] += 1
            return
        # 采集期间用户可能修改目标、条件或开关，分发前重新读个人订阅。
        feeds = list(await session.scalars(_active_feeds().where(CustomFeed.source_id == source_id,
            or_(CustomFeed.next_run_at.is_(None), CustomFeed.next_run_at <= utcnow()))
            .execution_options(populate_existing=True)))
        delivered = 0
        for feed in feeds:
            new_count = await distribute_custom_feed(session, feed)
            delivered += new_count; stats["feeds"] += 1; stats["new_items"] += new_count
            if new_count and feed.push_enabled:
                try:
                    stats["pushed"] += int(await push_custom_feed_digest(session, feed, new_count))
                except Exception:
                    await session.rollback()
                    logger.exception("custom digest failed for feed %s", feed.id)
        if run_id:
            run = await session.get(CustomCollectRun, run_id)
            run.status = "success"; run.finished_at = utcnow(); run.items_delivered = delivered
    except Exception as exc:
        await session.rollback()
        source = await session.get(CustomFeedSource, source_id, populate_existing=True)
        from app.services.bilibili_session import local_status, redact
        source.last_error = redact(str(exc), local_status()[1])[:1000]
        if isinstance(exc, CollectorDeferred):
            source.next_run_at = exc.retry_at
            stats["deferred"] += 1
        else:
            source.consecutive_failures += 1
            source.next_run_at = utcnow() + timedelta(minutes=min(interval * 2 ** min(source.consecutive_failures, 8), 1440))
            stats["failed"] += 1
            logger.warning("custom source %s failed: %s", source_id, source.last_error)
        if run_id:
            run = await session.get(CustomCollectRun, run_id)
            run.status = "deferred" if isinstance(exc, CollectorDeferred) else "failed"
            run.finished_at = utcnow(); run.error = source.last_error
    finally:
        source = await session.get(CustomFeedSource, source_id)
        source.lease_until = None
        await session.commit()


async def sweep_custom_feeds(session: AsyncSession, redis, limit: int = SWEEP_BATCH) -> dict:
    """每轮限独立目标数；共享采集后再按各用户自己的周期、过滤条件分发。"""
    # 兼容旧记录及进程内测试，迁移后正常创建路径已立即绑定 source_id。
    for feed in list(await session.scalars(_active_feeds().where(CustomFeed.source_id.is_(None)))):
        feed.source_id = (await shared_source(session, feed.platform, feed.kind, feed.target_value)).id
    await session.commit()
    now = utcnow()
    await session.execute(delete(CustomSourceItem).where(CustomSourceItem.collected_at < now - timedelta(days=30)))
    sources = list(await session.scalars(_active_feeds().with_only_columns(CustomFeed.source_id)
        .join(CustomFeedSource, CustomFeedSource.id == CustomFeed.source_id)
        .where(or_(CustomFeed.next_run_at.is_(None), CustomFeed.next_run_at <= now))
        .where(or_(CustomFeedSource.lease_until.is_(None), CustomFeedSource.lease_until < now))
        .where(or_(CustomFeedSource.next_run_at.is_(None), CustomFeedSource.next_run_at <= now,
            (CustomFeedSource.last_success_at.is_not(None) & CustomFeedSource.last_error.is_(None))))
        .group_by(CustomFeed.source_id).order_by(func.min(func.coalesce(CustomFeed.next_run_at, CustomFeed.created_at))).limit(limit)))
    await session.commit()
    stats = {"feeds": 0, "sources": 0, "new_items": 0, "pushed": 0, "failed": 0, "deferred": 0}
    for source_id in sources:
        await _run_shared_source(session, redis, source_id, stats)
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
