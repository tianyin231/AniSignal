"""频道必须在上限与归并之前生效，排队后也不得串平台或重放履约旧结论。"""

import copy
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentSchedule, ContentVersion, Event, Evidence, Product, ProductFulfillmentRevision, RawContent, Source
from app.services import bot, bot_briefs, bot_channels, bot_products
from app.services.bot_calendar import upcoming_entries


async def content(session, name, platform="bilibili", *, identity="official", when=None):
    now = when or utcnow() - timedelta(minutes=5)
    source = Source(platform=platform, external_id=name, identity=identity, name=name)
    session.add(source)
    await session.flush()
    raw = RawContent(platform=platform, external_id=name, community="wuwa", source_id=source.id,
                     content_type="video", published_at=now, collected_at=now, url=f"https://example.com/{name}")
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, version=1, parser_version="1", title=name,
                               text=f"{name} 独立来源公布的公开事实。", category="version_update", normalized_json={}))
    await session.commit()
    return raw


async def target(session, channels):
    body = TargetBody(name="测试会话", umo="mock:FriendMessage:" + channels[0].replace(":", "-"), enabled=True, channels=channels,
                      daily_enabled=False, updates_enabled=True, calendar_enabled=False, delivery_mode="astrbot")
    row = BotTarget(name=body.name, umo=body.umo, enabled=True,
                    config=body.model_dump(exclude={"name", "umo", "enabled"}),
                    last_scan_at=utcnow() - timedelta(minutes=20))
    session.add(row)
    await session.commit()
    return row


def test_registry_and_plugin_choices_agree():
    options = bot_channels.channel_options()
    schema = json.loads(Path("../integrations/astrbot_plugin_anisignal/_conf_schema.json").read_text(encoding="utf-8"))
    field = schema["targets"]["templates"]["session"]["items"]["subscription"]["items"]["channels"]
    assert dict(zip(field["options"], field["labels"])) == options
    assert options["wuwa:official:youtube"] == "鸣潮_官方_YB"
    body = TargetBody(name="test", umo="mock:FriendMessage:only", channels=["wuwa:official:youtube", "hsr:product"])
    assert body.communities == ["wuwa", "hsr"]
    with pytest.raises(ValidationError):
        TargetBody(name="test", umo="mock:FriendMessage:only", channels=["wuwa:official:invalid"])


async def test_platform_filtered_before_cross_platform_event_summary(session):
    bili = await content(session, "B站独有事实")
    youtube = await content(session, "YouTube独有事实", "youtube")
    event = Event(community="wuwa", title="包含B站事实的聚合标题", summary="不能混入YouTube频道的B站事实", category="version_update", identity="official")
    session.add(event)
    await session.flush()
    session.add_all([Evidence(event_id=event.id, raw_content_id=raw.id) for raw in (bili, youtube)])
    await session.commit()
    row = await target(session, ["wuwa:official:youtube"])
    payload = await bot.query_target(session, row, collected_after=utcnow() - timedelta(minutes=20))
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["platform"] == "youtube" and item["content_ids"] == [youtube.id]
    assert "B站事实" not in item["title"] + item["summary"]


async def test_platform_filter_precedes_latest_200_limit(session):
    youtube = await content(session, "older-youtube", "youtube", when=utcnow() - timedelta(hours=1))
    source = Source(platform="bilibili", external_id="busy-source", identity="official")
    session.add(source)
    await session.flush()
    for index in range(205):
        raw = RawContent(platform="bilibili", external_id=f"busy-{index}", community="wuwa", source_id=source.id,
                         content_type="video", published_at=utcnow() - timedelta(minutes=1))
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, version=1, parser_version="1", title=f"busy-{index}", text="公开内容", normalized_json={}))
    await session.commit()
    row = await target(session, ["wuwa:official:youtube"])
    candidates = await bot.load_target_candidates(session, row, None, utcnow()-timedelta(days=1), utcnow())
    assert [item["id"] for item in candidates] == [youtube.id]


async def test_official_product_matches_two_channels_once(session):
    raw = await content(session, "官方商品发货公告")
    product = Product(name="测试商品", slug="test-product", community="wuwa", watch_terms=["测试商品"])
    session.add(product)
    await session.flush()
    event = Event(community="wuwa", product_id=product.id, title="发货公告", category="shipping", identity="official")
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id))
    await session.commit()
    row = await target(session, ["wuwa:official:bilibili", "wuwa:product"])
    candidates = await bot.load_target_candidates(session, row, None, utcnow()-timedelta(days=1), utcnow())
    assert [item["id"] for item in candidates] == [raw.id]
    assert set(candidates[0]["notification_channels"]) == set(row.config["channels"])


async def test_community_and_official_channels_are_separate(session):
    official = await content(session, "official")
    community = await content(session, "community", identity="user")
    row = await target(session, ["wuwa:community:bilibili"])
    candidates = await bot.load_target_candidates(session, row, None, utcnow()-timedelta(days=1), utcnow())
    assert [item["id"] for item in candidates] == [community.id]
    assert official.id not in [item["id"] for item in candidates]
    updates = await bot.query_target(session, row, collected_after=utcnow()-timedelta(minutes=20))
    assert [item["id"] for item in updates["items"]] == [community.id]


async def test_calendar_filters_sources_before_merge(session):
    bili = await content(session, "bili")
    youtube = await content(session, "youtube", "youtube")
    now = utcnow()
    for raw in (bili, youtube):
        session.add(ContentSchedule(raw_content_id=raw.id, community="wuwa", title="相同活动", kind="activity", all_day=False,
                                    start_at=now-timedelta(days=1), end_at=now+timedelta(hours=1)))
    await session.commit()
    entries = await upcoming_entries(session, "wuwa", now, channels=["wuwa:official:youtube"])
    assert len(entries) == 1 and entries[0]["content_ids"] == [youtube.id]


async def test_queued_message_cancelled_after_channel_change(session):
    row = await target(session, ["wuwa:official:youtube"])
    source = {"id": 1, "community": "wuwa", "identity": "official", "platform": "bilibili", "text": "公开原文",
              "published_at": (utcnow()-timedelta(minutes=5)).isoformat()}
    delivery = BotDelivery(target_id=row.id, kind="updates", body="旧范围消息", dedupe_key="test-old-scope",
                           brief={"items": [{**source, "sources": [source]}]}, status="pending")
    session.add(delivery)
    await session.commit()
    assert not await bot.prepare_delivery(session, delivery, target=row)
    assert delivery.status == "cancelled"


async def test_code_notice_obeys_source_channel_before_queueing(session):
    from sqlalchemy import select
    from app.services.extract import _push_to_targets

    raw = await content(session, "B站兑换码公告")
    subscribed = await target(session, ["wuwa:official:bilibili"])
    await target(session, ["wuwa:official:youtube"])
    source = {"id": raw.id, "community": "wuwa", "identity": "official", "text": "公开兑换码公告"}
    count = await _push_to_targets(session, "wuwa", "critical", "兑换码提醒", "code:test", brief={"mode": "notice", "items": [{"sources": [source]}]})
    rows = list(await session.scalars(select(BotDelivery)))
    assert count == 1 and [row.target_id for row in rows] == [subscribed.id]


async def test_chat_channel_changes_are_validated_and_labeled(client, session):
    from app.services.settings import set_setting

    row = await target(session, ["wuwa:official:bilibili"])
    row.config = {**row.config, "chat_controls": True}
    await session.commit()
    await set_setting(session, "bot_bridge", {"tools_key": "test-channel-key"})
    response = await client.post("/api/bot/subscription", headers={"X-Bot-Key": "test-channel-key"},
        json={"umo": row.umo, "actor_id": "self", "action": "channels", "value": "鸣潮_官方_YB,鸣潮_商品"})
    assert response.status_code == 200
    assert response.json()["channels"] == ["wuwa:official:youtube", "wuwa:product"]
    assert response.json()["channel_labels"] == ["鸣潮_官方_YB", "鸣潮_商品"]


async def test_fulfillment_change_reuses_shared_budget_and_revalidates_evidence(session, monkeypatch):
    raw = await content(session, "测试键盘官方发货公告")
    product = Product(name="测试键盘", slug="test-keyboard", community="wuwa", watch_terms=["测试键盘"])
    session.add(product)
    await session.flush()
    event = Event(community="wuwa", product_id=product.id, title="发货公告", category="shipping", identity="official")
    session.add(event)
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id))
    snapshot = {"official": {"state": "shipping", "state_label": "开始发货", "verified_count": 1},
                "consumer": {"state": "no_verified_receipt", "state_label": "暂无已采纳到货", "verified_count": 0},
                "quality": {"state": "candidate_attention", "state_label": "未核验风险", "verified_count": 0},
                "sample_count": 0, "observation_count": 0}
    previous = copy.deepcopy(snapshot)
    previous["official"]["verified_count"] = 0
    revision = ProductFulfillmentRevision(product_id=product.id, community="wuwa", previous_snapshot=previous,
                                          snapshot=snapshot, reason="采纳发货证据", created_at=utcnow()-timedelta(minutes=1))
    session.add(revision)
    await session.commit()

    async def signal_data(*args):
        return ([(SimpleNamespace(review_status="verified"), SimpleNamespace(role="primary"), raw, None)], {})

    async def projection(*args, **kwargs):
        return snapshot

    async def no_model(*args, **kwargs):
        raise AssertionError("履约修订不应再次请求摘要模型")

    monkeypatch.setattr(bot_products, "load_product_signal_data", signal_data)
    monkeypatch.setattr(bot_products, "build_fulfillment_snapshot", projection)
    monkeypatch.setattr(bot_briefs, "select_brief_items", no_model)
    row = await target(session, ["wuwa:product", "wuwa:official:bilibili"])
    payload = await bot.query_target(session, row, collected_after=utcnow()-timedelta(minutes=20))
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["fulfillment_revision"] == revision.id
    assert "开始发货" in item["text"] and "未核验风险" not in item["text"]
    brief = await bot_briefs.build_shared_brief(session, [item])
    delivery = BotDelivery(target_id=row.id, kind="updates", body="履约变化", dedupe_key="test-fulfillment", brief=brief)
    session.add(delivery)
    await session.commit()
    assert await bot.prepare_delivery(session, delivery, target=row)
    raw.hidden = True
    assert not await bot.prepare_delivery(session, delivery, target=row)
    assert delivery.status == "cancelled"
