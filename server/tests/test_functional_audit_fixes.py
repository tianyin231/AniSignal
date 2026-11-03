"""筛选后取数、早报限频与监控资料刷新的业务回归。"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentVersion, CustomFeed, CustomFeedItem, CustomSourceItem, MetricSnapshot, RawContent, RedeemCode, Source
from app.schemas.content import NormalizedAuthor, NormalizedContent
from app.services import bot, custom_feeds
from app.services.bot_policy import reserve_delivery
from app.services.curation import select_trending
from app.services.custom_policy import shared_source


async def content(session, name, *, identity="official", text="公开活动资料"):
    source = Source(platform="bilibili", external_id=name, name=name, identity=identity)
    session.add(source)
    await session.flush()
    raw = RawContent(platform="bilibili", content_type="video", external_id=name,
                     source_id=source.id, community="wuwa", published_at=utcnow() - timedelta(hours=3),
                     url=f"https://example.com/{name}")
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, title=name, text=text, category="guide", normalized_json={}))
    return raw


@pytest.mark.parametrize("filters", [{"keywords": ["目标"]}, {"exclude_keywords": ["排除"]}])
async def test_codes_filter_before_eight_result_limit(session, filters):
    now = utcnow()
    for index in range(18):
        raw = await content(session, f"{'目标活动' if index < 9 else '排除活动'}-{index}")
        session.add(RedeemCode(raw_content_id=raw.id, community="wuwa", code=f"CODE{index:02}",
                               expires_at=now + timedelta(days=1), created_at=now + timedelta(seconds=index)))
    target = BotTarget(name="兑换码筛选", umo="qq:GroupMessage:codes", config={"community": "wuwa", **filters})
    session.add(target)
    await session.commit()
    result = await bot.query_target(session, target, tool="codes", now=now)
    assert [line.split("（")[0] for line in result["text"].splitlines() if line.startswith("CODE")] == [
        f"CODE{index:02}" for index in range(8, 0, -1)
    ]


@pytest.mark.parametrize("filters", [
    {"channels": ["wuwa:official:bilibili"], "scope_mode": "channels"},
    {"keywords": ["正文目标词"]},
    {"exclude_keywords": ["排除活动"]},
])
async def test_trending_filter_before_top_five_and_keep_public_ranking(session, filters):
    now = utcnow()
    ids = []
    for index in range(11):
        wanted = index >= 5
        raw = await content(session, f"{'符合订阅' if wanted else '排除活动'}-{index}",
                            identity="official" if wanted else "community",
                            text="正文资料。" * 40 + ("正文目标词" if wanted else "无关内容"))
        ids.append(raw.id)
        session.add_all([
            MetricSnapshot(raw_content_id=raw.id, metrics={"view": 100}, captured_at=now - timedelta(hours=2)),
            MetricSnapshot(raw_content_id=raw.id, metrics={"view": 20000 - index * 1000}, captured_at=now - timedelta(hours=1)),
        ])
    target = BotTarget(name="热度筛选", umo="qq:GroupMessage:trending", config={"community": "wuwa", **filters})
    session.add(target)
    await session.commit()
    result = await bot.query_target(session, target, tool="trending", now=now)
    assert [item["id"] for item in result["items"]] == ids[5:10]
    assert [item["id"] for item in await select_trending(session, 5, community="wuwa")] == ids[:5]


@pytest.mark.parametrize("hourly_limit,gap,item_count,late_minutes,expected_messages", [
    (1, 20, 2, 0, 1),
    (0, 60, 2, 0, 1),
    (2, 20, 2, 100, 1),
    (0, 120, 1, 0, 1),
    (2, 20, 2, 0, 3),
    (1, 20, 1, 0, 2),
])
async def test_split_daily_falls_back_to_complete_digest_when_window_is_too_short(
    session, monkeypatch, hourly_limit, gap, item_count, late_minutes, expected_messages,
):
    due = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    target = BotTarget(name="限频早报", umo="qq:GroupMessage:daily", enabled=True,
                       created_at=due - timedelta(days=1), last_scan_at=due,
                       config={"community": "wuwa", "daily_enabled": True, "daily_time": "08:00",
                               "daily_style": "split", "updates_enabled": False, "delivery_mode": "astrbot",
                               "hourly_limit": hourly_limit, "min_push_interval_minutes": gap,
                               "quiet_start": 0, "quiet_end": 0})
    session.add(target)
    await session.commit()
    items = [{"id": index, "title": f"活动公告{index}", "text": f"活动{index}开放领取奖励。",
              "community": "wuwa", "identity": "official", "platform": "kuro_cn",
              "published_at": due - timedelta(hours=1), "url": f"https://example.com/{index}"}
             for index in range(1, item_count + 1)]
    brief = {"mode": "daily", "title": "今日速报", "date": (due + timedelta(hours=8)).strftime("%m/%d"),
             "items": bot.fallback_items(items)}
    monkeypatch.setattr(bot, "query_target", AsyncMock(return_value={"text": bot.format_brief(brief), "items": items, "brief": brief}))
    now = due + timedelta(minutes=late_minutes)
    await bot.run_bot_notifications(session, now)
    rows = list(await session.scalars(select(BotDelivery).order_by(BotDelivery.id)))
    assert len(rows) == expected_messages
    if expected_messages == 1:
        assert len(rows[0].brief["items"]) == item_count
        assert all(item["title"] in rows[0].body for item in items)
        assert await reserve_delivery(session, target, rows[0], now=now)
    # 同一轮再次调度不能为合并或拆条早报重复建消息。
    await bot.run_bot_notifications(session, now + timedelta(seconds=15))
    assert await session.scalar(select(func.count(BotDelivery.id))) == expected_messages


async def test_existing_custom_items_refresh_without_becoming_new_notifications(session, client):
    source = await shared_source(session, "bilibili", "keyword", "资料更新")
    feed = CustomFeed(source_id=source.id, name="资料更新", kind="keyword", platform="bilibili",
                      target_value="资料更新", is_public=True)
    session.add(feed)
    await session.flush()
    original = NormalizedContent(platform="bilibili", content_type="video", external_id="BV-refresh",
                                 title="原始标题", text="公开资料", url="https://example.com/original",
                                 media=["https://example.com/old.jpg"], metrics={"view": 10000},
                                 author=NormalizedAuthor(name="原作者"), published_at=utcnow() - timedelta(hours=1))
    cached = CustomSourceItem(source_id=source.id, external_id=original.external_id, normalized=original.model_dump(mode="json"))
    session.add(cached)
    await session.commit()
    assert await custom_feeds.distribute_custom_feed(session, feed) == 1
    row = await session.scalar(select(CustomFeedItem).where(CustomFeedItem.feed_id == feed.id))
    first_collected = row.collected_at
    cached.normalized = original.model_copy(update={
        "title": "已更正标题", "url": "https://example.com/corrected", "media": ["https://example.com/new.jpg"],
        "metrics": {"view": 50000}, "author": NormalizedAuthor(name="新作者"),
    }).model_dump(mode="json")
    await session.commit()
    assert await custom_feeds.distribute_custom_feed(session, feed) == 0
    assert row.collected_at == first_collected
    assert await session.scalar(select(func.count(CustomFeedItem.id))) == 1
    response = await client.get(f"/api/custom/feeds/{feed.id}")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert (item["title"], item["view"], item["author_name"]) == ("已更正标题", 50000, "新作者")
    assert item["url"] == "https://example.com/corrected" and item["cover"] == "https://example.com/new.jpg"
