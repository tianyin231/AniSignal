"""通知以仍可行动为前提，共享预算，不在恢复服务后补刷旧消息。"""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentSchedule, Event, Evidence, RawContent, RedeemCode
from app.services import bot
from app.services.bot_calendar import calendar_brief, upcoming_entries
from app.services.bot_policy import reserve_delivery
from app.services.extract import _push_to_targets
from app.services.settings import set_setting


async def test_empty_editor_does_not_lose_fresh_official_costume_news(session, monkeypatch):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now, community='arknights')
    item = await bot.identify_item(session, {
        'id': 9580, 'community': 'arknights', 'identity': 'official', 'title': '【新增服饰】',
        'text': '【新增服饰】\n//夜行烛火 - 烛煌\n国家图书馆合作服装/夜行烛火。煌在游历炎国各方异域时所穿的服装。',
        'published_at': now, 'url': 'https://example.com/costume',
    })
    monkeypatch.setattr('app.services.bot_briefs.get_ai_config', AsyncMock(return_value={
        'api_key': 'fake', 'base_url': 'http://model', 'model': 'test',
    }))
    monkeypatch.setattr('app.services.bot_briefs.chat', AsyncMock(return_value=('{"items":[]}', {})))
    buffer = {'items': [{**bot._source(item), 'buffered_at': now.isoformat()}], 'flush_at': now.isoformat()}
    await bot._flush_merge_buffer(session, row, buffer, now, quiet=False, quiet_critical=False,
                                  hourly_limit=0, max_images=1)
    deliveries = list(await session.scalars(select(BotDelivery).where(BotDelivery.target_id == row.id)))
    assert len(deliveries) == 1 and deliveries[0].status == 'pending'
    assert '夜行烛火' in deliveries[0].body and '烛煌' in deliveries[0].body
    assert (await bot.load_merge_buffer(session, row.id))['items'] == []


async def test_merge_retains_news_not_selected_in_this_batch(session, monkeypatch):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    items = [await bot.identify_item(session, {
        'id': index, 'community': 'wuwa', 'identity': 'official', 'title': f'新增活动{index}',
        'text': f'活动{index}将于10月15日开启。', 'published_at': now,
    }) for index in (1, 2, 3)]

    async def one_item(session, candidates, *args, **kwargs):
        return {'mode': 'updates', 'items': bot.fallback_items(candidates[:1], limit=1)}

    monkeypatch.setattr(bot, 'build_shared_brief', one_item)
    buffer = {'items': [{**bot._source(item), 'buffered_at': now.isoformat()} for item in items],
              'flush_at': now.isoformat()}
    for index in range(3):
        await bot._flush_merge_buffer(session, row, buffer, now + timedelta(minutes=index * 5),
                                      quiet=False, quiet_critical=False, hourly_limit=0, max_images=1)
        buffer = await bot.load_merge_buffer(session, row.id)
        assert len(buffer['items']) == 2 - index
    deliveries = list(await session.scalars(select(BotDelivery).where(BotDelivery.target_id == row.id)))
    sent_ids = [cid for delivery in deliveries for item in delivery.brief['items'] for cid in item['content_ids']]
    assert sorted(sent_ids) == [1, 2, 3]


async def test_empty_editor_does_not_force_community_news_or_followups(session, monkeypatch):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    items = [await bot.identify_item(session, {
        'id': index, 'community': 'wuwa', 'identity': identity, 'title': '活动体验',
        'text': '活动体验有所变化。', 'published_at': now, 'is_followup': followup,
    }) for index, identity, followup in [(1, 'user', False), (2, 'official', True)]]

    async def empty(*args, **kwargs):
        return {'mode': 'updates', 'items': []}

    monkeypatch.setattr(bot, 'build_shared_brief', empty)
    buffer = {'items': [{**bot._source(item), 'buffered_at': now.isoformat()} for item in items],
              'flush_at': now.isoformat()}
    await bot._flush_merge_buffer(session, row, buffer, now, quiet=False, quiet_critical=False,
                                  hourly_limit=0, max_images=1)
    assert not list(await session.scalars(select(BotDelivery).where(BotDelivery.target_id == row.id)))
    assert (await bot.load_merge_buffer(session, row.id))['items'] == []


@pytest.mark.parametrize('empty_editor', [False, True])
async def test_shared_news_is_edited_once_and_dispatched_immediately(session, monkeypatch, empty_editor):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    targets = [await target(session, now, community='arknights', umo=f'qq:GroupMessage:shared-{i}') for i in range(3)]
    item = {
        'id': 9580, 'community': 'arknights', 'identity': 'official', 'title': '【新增服饰】',
        'text': '【新增服饰】\n//夜行烛火 - 烛煌\n国家图书馆合作服装/夜行烛火。煌在游历炎国各方异域时所穿的服装。',
        'published_at': now, 'url': 'https://example.com/costume',
    }

    async def query(*args, **kwargs):
        return {'items': [item]}

    response = {'items': [] if empty_editor else [{
        'indices': [1], 'title': '夜行烛火新服饰',
        'summary': '明日方舟公布烛煌的国家图书馆合作服饰「夜行烛火」。',
    }]}
    chat = AsyncMock(side_effect=[(json.dumps(response, ensure_ascii=False), {}), ('{"items":[]}', {})])
    monkeypatch.setattr(bot, 'query_target', query)
    monkeypatch.setattr('app.services.bot_briefs.get_ai_config', AsyncMock(return_value={
        'api_key': 'fake', 'base_url': 'http://model', 'model': 'test',
    }))
    monkeypatch.setattr('app.services.bot_briefs.chat', chat)
    await bot.run_bot_notifications(session, now)
    deliveries = list(await session.scalars(select(BotDelivery).order_by(BotDelivery.id)))
    assert chat.await_count == 1
    assert [d.target_id for d in deliveries] == [t.id for t in targets]
    assert all(d.kind == 'updates' and d.status == 'pending' for d in deliveries)
    assert len({d.body for d in deliveries}) == 1
    assert '夜行烛火' in deliveries[0].body and '烛煌' in deliveries[0].body
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    assert chat.await_count == 1
    assert len(list(await session.scalars(select(BotDelivery)))) == 3


async def test_new_merge_items_are_persisted_before_the_scan_advances(session, session_factory, monkeypatch):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now, merge_minutes=10)
    count = 0

    async def query(*args, **kwargs):
        nonlocal count
        count += 1
        return {'items': [{'id': count, 'community': 'wuwa', 'identity': 'official',
                           'title': f'新增活动{count}', 'text': f'活动{count}将于10月15日开启。',
                           'published_at': now}]}

    monkeypatch.setattr(bot, 'query_target', query)
    await bot.run_bot_notifications(session, now)
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    async with session_factory() as restarted:
        buffer = await bot.load_merge_buffer(restarted, row.id)
        assert [item['id'] for item in buffer['items']] == [1, 2]
        assert (await restarted.get(BotTarget, row.id)).last_scan_at == now + timedelta(minutes=1)
    assert not list(await session.scalars(select(BotDelivery)))


async def target(session, now, *, umo="qq:GroupMessage:test", **config):
    row = BotTarget(name="测试", umo=umo, enabled=True, created_at=now - timedelta(days=1),
                    last_scan_at=now, config={"updates_enabled": True, "daily_enabled": False,
                    "delivery_mode": "astrbot", "community": "wuwa", "quiet_start": 23, "quiet_end": 8,
                    "hourly_limit": 2, "min_push_interval_minutes": 20, **config})
    session.add(row)
    await session.commit()
    return row


async def schedule(session, now, *, title="活动", kind="activity", end=None):
    raw = RawContent(platform="kuro_cn", content_type="notice", external_id=title, community="wuwa", url="https://example.com/source")
    session.add(raw)
    await session.flush()
    row = ContentSchedule(raw_content_id=raw.id, community="wuwa", title=title, kind=kind,
                          start_at=now + timedelta(hours=5) if kind in {"maintenance", "deadline"} else now - timedelta(days=1),
                          end_at=end or now + timedelta(hours=8))
    session.add(row)
    await session.commit()
    return row


async def test_calendar_one_short_digest_deduplicates_actual_titles_and_has_full_detail(session):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    titles = ["若梦仍有回声", "[若梦仍有回声]限时联机战斗活动", "雾隐阁特别勘察",
              "雾隐阁特别勘察限时探索活动", "声骸材料限时双倍活动", "[声弦涤荡]声骸材料限时双倍活动"]
    for title in titles:
        await schedule(session, now, title=title)
    await schedule(session, now, title="已经结束", end=now - timedelta(minutes=1))
    await schedule(session, now, title="直播结束", kind="broadcast")
    await schedule(session, now, title="更新维护", kind="maintenance")
    await schedule(session, now, title="报名截止", kind="deadline")
    await bot.run_bot_notifications(session, now)
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    deliveries = list(await session.scalars(select(BotDelivery)))
    assert len(deliveries) == 1 and deliveries[0].kind == "calendar"
    delivery = deliveries[0]
    entries = delivery.brief["items"][0]["calendar_entries"]
    assert len(entries) == 5
    assert {entry["title"] for entry in entries} == {titles[0], titles[2], titles[4], "更新维护", "报名截止"}
    assert sum(len(entry["schedule_ids"]) for entry in entries) == 8
    assert len(delivery.body) <= 190 and "/展开" not in delivery.body
    assert delivery.brief['truncated'] is True
    assert '想参与的' not in delivery.body
    assert "未领取" not in delivery.body and "已经结束" not in delivery.body
    assert all(entry["url"] and entry["expires_at"] for entry in entries)


async def test_calendar_never_pierces_quiet_and_expired_legacy_is_cancelled(session):
    now = utcnow().replace(hour=17, minute=0, second=0, microsecond=0)  # 北京次日 01:00
    row = await target(session, now, quiet_critical=True, calendar_time="01:00")
    entry = await schedule(session, now)
    await bot.run_bot_notifications(session, now)
    assert not list(await session.scalars(select(BotDelivery)))
    legacy = await bot.create_delivery(session, row, "calendar", "旧提醒", f"calendar:{row.id}:{entry.id}:activity")
    assert not await reserve_delivery(session, row, legacy, now=now)
    assert legacy.status == "pending" and legacy.attempts == 0
    entry.end_at = now - timedelta(seconds=1)
    await session.commit()
    assert not await reserve_delivery(session, row, legacy, now=now)
    assert legacy.status == "cancelled"


async def test_calendar_refresh_removes_expired_and_changed_items(session):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    expired = await schedule(session, now, title="先结束", end=now + timedelta(minutes=1))
    changed = await schedule(session, now, title="时间调整")
    await schedule(session, now, title="仍可参加")
    text, brief = calendar_brief(await upcoming_entries(session, "wuwa", now), now)
    delivery = await bot.create_delivery(session, row, "calendar", text, "calendar-digest:test", brief=brief)
    changed.end_at += timedelta(days=1)
    await session.commit()
    assert await reserve_delivery(session, row, delivery, now=now + timedelta(minutes=2))
    assert [e["title"] for e in delivery.brief["items"][0]["calendar_entries"]] == ["仍可参加"]
    assert "先结束" not in delivery.body and "时间调整" not in delivery.body


async def test_actual_send_time_budget_and_gap_apply_to_critical_but_not_manual(session):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    previous = await bot.create_delivery(session, row, "updates", "之前已发", "previous")
    previous.status = "accepted"
    previous.created_at = now - timedelta(days=2)
    previous.updated_at = now - timedelta(minutes=3)
    await session.commit()
    critical = await bot.create_delivery(session, row, "critical", "重要", "important")
    assert not await reserve_delivery(session, row, critical, now=now)
    manual = await bot.create_delivery(session, row, "manual", "手工", "manual")
    assert await reserve_delivery(session, row, manual, now=now)
    row.config = {**row.config, "min_push_interval_minutes": 0, "hourly_limit": 1}
    await session.commit()
    assert not await reserve_delivery(session, row, critical, now=now)
    previous.status = "failed"  # 失败未送达，不消耗自动消息预算。
    await session.commit()
    assert await reserve_delivery(session, row, critical, now=now)


async def test_concurrent_claims_reserve_only_one_auto_message(session, session_factory):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    first = await bot.create_delivery(session, row, "updates", "第一条", "concurrent1")
    second = await bot.create_delivery(session, row, "critical", "第二条", "concurrent2")

    async def reserve(delivery):
        async with session_factory() as other:
            return await reserve_delivery(other, row, delivery, now=now, receipt=str(delivery.id))

    assert sorted(await asyncio.gather(reserve(first), reserve(second))) == [False, True]
    rows = list(await session.scalars(select(BotDelivery).execution_options(populate_existing=True)))
    assert sorted(r.status for r in rows) == ["pending", "sending"]


async def test_pending_disabled_and_expired_messages_are_cancelled_on_claim(client, session, monkeypatch):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.api.bot_companion.utcnow", lambda: now)
    await set_setting(session, "bot_bridge", {"tools_key": "test"})
    row = await target(session, now)
    old = await bot.create_delivery(session, row, "critical", "过期积压", "old")
    old.created_at = now - timedelta(hours=13)
    daily = await bot.create_delivery(session, row, "daily", "晚到的早报", "old-daily",
        brief={"scheduled_at": (now - timedelta(hours=4)).isoformat(), "items": [{"title": "早报"}]})
    row.config = {**row.config, "daily_enabled": True}
    await session.commit()
    assert (await client.post("/api/bot/outbox/claim", headers={"X-Bot-Key": "test"}, json={})).json()["delivery"] is None
    await session.refresh(old)
    await session.refresh(daily)
    assert old.status == daily.status == "cancelled"
    pending = await bot.create_delivery(session, row, "updates", "关闭后不补", "disabled")
    row.config = {**row.config, "updates_enabled": False}
    await session.commit()
    assert not await reserve_delivery(session, row, pending, now=now)
    assert pending.status == "cancelled"


async def test_cross_window_only_source_changes_create_a_followup(session, monkeypatch):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now, merge_minutes=1)
    event = Event(title="官方合作影像", category="announcement", conclusion="合作影像已发布")
    session.add(event)
    await session.commit()
    count = 0
    source_text = "新的合作影像已发布。"

    async def query(*args, **kwargs):
        nonlocal count
        count += 1
        return {"items": [{"id": count, "content_ids": [count], "event_id": event.id,
                           "title": "官方合作影像", "text": source_text, "identity": "official",
                           "community": "wuwa", "published_at": now, "url": "https://example.com/new"}]}

    monkeypatch.setattr(bot, "query_target", query)
    for minute in (0, 2, 5, 7):
        await bot.run_bot_notifications(session, now + timedelta(minutes=minute))
    rows = list(await session.scalars(select(BotDelivery)))
    assert len(rows) == 1
    assert rows[0].brief["notification_keys"] and rows[0].brief["items"][0]["event_id"] == event.id
    rows[0].status, rows[0].updated_at = 'accepted', now + timedelta(minutes=7)
    event.conclusion = "合作影像增加了正式上线日期"
    await session.commit()
    for minute in (10, 12):
        await bot.run_bot_notifications(session, now + timedelta(minutes=minute))
    assert len(list(await session.scalars(select(BotDelivery)))) == 1
    source_text += '视频追加10月10日上线时间。'
    for minute in (15, 17):
        await bot.run_bot_notifications(session, now + timedelta(minutes=minute))
    rows = list(await session.scalars(select(BotDelivery).order_by(BotDelivery.id)))
    assert len(rows) == 2 and rows[-1].brief['items'][0]['is_followup']
    assert '补充' in rows[-1].body and '10月10日' in rows[-1].body
    assert '新的合作影像已发布' not in rows[-1].body


async def test_empty_and_late_daily_never_backfill(session):
    now = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    await target(session, now, daily_enabled=True, updates_enabled=False)
    await bot.run_bot_notifications(session, now)
    await bot.run_bot_notifications(session, now + timedelta(hours=5))
    assert not list(await session.scalars(select(BotDelivery)))


async def test_legacy_brief_dedupe_sources_and_content_event_mapping(session):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    event = Event(title="合作影像", category="announcement", conclusion="合作影像发布")
    raw = RawContent(platform="kuro_cn", content_type="notice", external_id="legacy", community="wuwa")
    session.add_all([event, raw])
    await session.flush()
    session.add(Evidence(event_id=event.id, raw_content_id=raw.id, role="supporting"))
    await session.commit()
    old = await bot.create_delivery(session, row, "updates", "旧短讯", "updates:legacy-window",
        brief={"items": [{"title": "旧短讯", "sources": [{"content_ids": [raw.id]}, {"dedupe": "exact-fingerprint"}]}]})
    old.status = "accepted"
    await session.commit()
    known = await bot.known_notification_keys(session, row.id)
    assert "exact-fingerprint" in known
    assert bot.item_was_notified(await bot.identify_item(session, {"id": raw.id + 1, "event_id": event.id}), known)


async def test_extract_target_dedupe_and_expiry_guard(session):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    first = await target(session, now)
    second = BotTarget(name="另一目标", umo="qq:FriendMessage:other", enabled=True, config=first.config)
    session.add(second)
    await session.commit()
    for _ in range(2):
        await _push_to_targets(session, "wuwa", "critical", "新码 TESTCODE", "code:wuwa:TESTCODE",
            brief={"mode": "notice", "code": "TESTCODE", "expires_at": (now + timedelta(hours=1)).isoformat(), "items": []})
    rows = list(await session.scalars(select(BotDelivery)))
    assert len(rows) == 2 and {r.target_id for r in rows} == {first.id, second.id}
    raw = RawContent(platform="kuro_cn", content_type="notice", external_id="code", community="wuwa")
    session.add(raw)
    await session.flush()
    session.add(RedeemCode(raw_content_id=raw.id, community="wuwa", code="TESTCODE", status="active", expires_at=now - timedelta(minutes=1)))
    await session.commit()
    assert not await reserve_delivery(session, first, rows[0], now=now)
    assert rows[0].status == "cancelled"


async def test_real_video_dynamic_repost_dedupes_legacy_raw_title_for_72_hours(session):
    now = utcnow()
    row = await target(session, now)
    title = "鸣潮云游中国特别合作影像  | 观潮览胜，入梦山河"
    old = await bot.create_delivery(session, row, "updates", "云游中国", "old-window",
        brief={"items": [{"title": "AI 短标题", "sources": [{"id": 6716, "content_ids": [6716],
            "identity": "official", "community": "wuwa", "title": title, "published_at": now.isoformat()}]}]})
    old.status = "accepted"
    await session.commit()
    new = await bot.identify_item(session, {"id": 6758, "title": title.replace("  |", "|"), "identity": "official", "community": "wuwa"})
    assert bot.item_was_notified(new, await bot.known_notification_keys(session, row.id, now))
    assert not bot.item_was_notified(new, await bot.known_notification_keys(session, row.id, now + timedelta(hours=73)))
    assert not bot.notification_topic({"identity": "official", "title": "◆活动时间注意◆", "community": "wuwa"})
    assert not bot.notification_topic({**new, "identity": "community"})


def test_history_keys_only_record_actual_selected_sources():
    items = [{"id": 1, "dedupe": "selected", "identity": "official", "title": "这个标题属于真的入选消息", "community": "wuwa"},
             {"id": 2, "dedupe": "not-selected", "identity": "official", "title": "这个标题是未入选候选消息", "community": "wuwa"}]
    brief = {"items": [{"title": "AI 改写标题", "sources": [{**items[0], "content_ids": [1]}]}]}
    keys = bot.notification_brief(brief, items)["notification_keys"]
    assert "selected" in keys and "not-selected" not in keys
    assert bot.notification_topic(items[0]) in keys and bot.notification_topic(items[1]) not in keys


async def test_calendar_skips_date_only_and_maintenance_copy_is_relevant(session):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await target(session, now)
    date_only = await schedule(session, now, title="只写了日期的活动")
    date_only.all_day = True
    await session.commit()
    await schedule(session, now, title="3.7版本更新维护", kind="maintenance")
    entries = await upcoming_entries(session, "wuwa", now)
    text, brief = calendar_brief(entries, now)
    assert len(entries) == 1 and entries[0]["kind"] == "maintenance"
    assert "开始维护" in text and '3.7版本更新维护' in text
    assert '想参与' not in text and '/展开' not in text and brief['truncated'] is False


async def test_queued_daily_uses_current_fact_template_without_rewriting_accepted_history(session):
    now=utcnow().replace(hour=0,minute=0,second=0,microsecond=0)
    row=await target(session,now,daily_enabled=True,daily_time='08:00')
    brief={'mode':'daily','title':'今日速报','date':'10/25','scheduled_at':now.isoformat(),
           'items':[{'title':'官方新公告','community':'wuwa','summary':'活动将开放',
                     'sources':[{'text':'官方新公告：活动将开放。','community':'wuwa'}]}]}
    pending=BotDelivery(target_id=row.id,kind='daily',body='旧正文\n想细看，发 /展开 1',
                        brief=brief,dedupe_key='queued-before-template-upgrade',created_at=now)
    history=BotDelivery(target_id=row.id,kind='daily',body='历史正文\n想细看，发 /展开 1',
                        brief=brief,status='accepted',dedupe_key='accepted-before-template-upgrade',
                        created_at=now-timedelta(days=1),updated_at=now-timedelta(days=1))
    session.add_all([pending,history]);await session.commit()
    assert await reserve_delivery(session,row,pending,now=now)
    assert '官方新公告' in pending.body and '/展开' not in pending.body
    await session.refresh(history)
    assert history.body=='历史正文\n想细看，发 /展开 1'


async def test_same_window_video_dynamic_repost_enters_buffer_once(session, monkeypatch):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    row = await target(session, now, merge_minutes=1)
    title = "鸣潮云游中国特别合作影像 | 观潮览胜，入梦山河"

    async def query(*args, **kwargs):
        return {"items": [{"id": cid, "content_ids": [cid], "title": title, "text": "特别合作影像已发布。",
                           "identity": "official", "community": "wuwa", "published_at": now} for cid in (6716, 6758)]}

    monkeypatch.setattr(bot, "query_target", query)
    await bot.run_bot_notifications(session, now)
    assert len((await bot.load_merge_buffer(session, row.id))["items"]) == 1
    await bot.run_bot_notifications(session, now + timedelta(minutes=2))
    rows = list(await session.scalars(select(BotDelivery)))
    assert len(rows) == 1 and len(rows[0].brief["items"]) == 1


async def test_source_age_is_checked_when_flushing_and_claiming_without_resetting_it(session):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now, quiet_start=0, quiet_end=0, hourly_limit=0, min_push_interval_minutes=0)
    old = {'id': 1, 'title': '旧版预告', 'text': '预告已经发布。', 'dedupe': 'old-source',
           'community': 'wuwa', 'published_at': (now - timedelta(hours=25)).isoformat(), 'buffered_at': now.isoformat()}
    fresh = {**old, 'id': 2, 'title': '新版预告', 'dedupe': 'fresh-source', 'published_at': now.isoformat()}
    unknown = {**old, 'id': 3, 'title': '未知时间', 'dedupe': 'unknown-source', 'published_at': None}
    buffer = {'items': [old, unknown, fresh], 'flush_at': now.isoformat()}
    await bot._flush_merge_buffer(session, row, buffer, now, quiet=False, quiet_critical=False, hourly_limit=0, max_images=1)
    merged = await session.scalar(select(BotDelivery))
    assert [i['id'] for i in merged.brief['items']] == [2]
    assert '旧版' not in merged.body and '未知时间' not in merged.body
    stale = await bot.create_delivery(session, row, 'critical', '旧预告', 'stale', brief=bot.single_item_brief(old))
    missing = await bot.create_delivery(session, row, 'updates', '未知时间', 'missing', brief=bot.single_item_brief(unknown))
    for delivery in (stale, missing):
        assert not await reserve_delivery(session, row, delivery, now=now)
        assert delivery.status == 'cancelled' and delivery.attempts == 0
    # 排队仍在 12 小时内，也不能把超过发布时间窗口的内容继续推送。
    almost_old = {**old, 'published_at': (now - timedelta(hours=23)).isoformat()}
    queued = await bot.create_delivery(session, row, 'updates', '排队中的预告', 'queued', brief=bot.single_item_brief(almost_old))
    queued.created_at = now
    await session.commit()
    assert not await reserve_delivery(session, row, queued, now=now + timedelta(hours=2))
    assert queued.status == 'cancelled'
    # 删掉合并快照里的旧条目，图片、正文、去重键都必须跟着刷新。
    mixed = await bot.create_delivery(session, row, 'updates', '旧版预告和新版预告', 'mixed',
        brief={'mode': 'updates', 'items': [old, fresh], 'notification_keys': ['old-source', 'fresh-source']})
    assert await reserve_delivery(session, row, mixed, now=now)
    assert '旧版' not in mixed.body and mixed.brief['notification_keys'] == ['fresh-source']
    # 带明确早报刊期和有效期的补充项可以在早报窗口内发送。
    daily_part = await bot.create_delivery(session, row, 'updates', '补充 · 旧版预告', 'daily:part',
        brief={**bot.single_item_brief(old), 'scheduled_at': now.isoformat(),
               'expires_at': (now + timedelta(hours=2)).isoformat()})
    assert await reserve_delivery(session, row, daily_part, now=now)
    assert daily_part.status == 'sending'


async def test_followup_history_is_scoped_to_delivered_messages_in_the_same_group(session):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    first = await target(session, now)
    second = BotTarget(name='另一群', umo='qq:GroupMessage:second', enabled=True, config=first.config)
    session.add(second)
    await session.commit()
    original = {'id': 10, 'event_id': 77, 'community': 'wuwa', 'identity': 'official', 'title': '限时活动',
                'text': '活动10月12日18:00截止。奖励包含500星声。', 'published_at': now}
    new = {**original, 'id': 11, 'text': '活动10月15日18:00截止。奖励包含500星声。'}
    failed = await bot.create_delivery(session, first, 'updates', '旧消息', 'old-failed', brief=bot.single_item_brief(original))
    failed.status = 'failed'
    await session.commit()
    prepared = await bot.prepare_update_items(session, first.id, [new], now, set())
    assert not prepared[0].get('is_followup')
    failed.status, failed.updated_at = 'accepted', now
    await session.commit()
    prepared = await bot.prepare_update_items(session, first.id, [new], now, set())
    assert prepared[0]['is_followup'] and prepared[0]['new_points'] == ['活动10月15日18:00截止。']
    assert not (await bot.prepare_update_items(session, second.id, [new], now, set()))[0].get('is_followup')
    other_game = {**new, 'community': 'blhx'}
    assert not (await bot.prepare_update_items(session, first.id, [other_game], now, set()))[0].get('is_followup')
    assert await bot.prepare_update_items(session, first.id, [original], now, set()) == []


async def test_unsent_merge_buffer_keeps_latest_facts_once(session, monkeypatch):
    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now, merge_minutes=5)
    event = Event(title='测试活动', category='announcement', conclusion='官方活动公告')
    session.add(event)
    await session.commit()
    current = {'id': 10, 'event_id': event.id, 'community': 'wuwa', 'identity': 'official', 'title': '测试活动',
               'text': '活动10月12日截止。', 'published_at': now}
    async def query(*args, **kwargs):
        return {'items': [current]}
    monkeypatch.setattr(bot, 'query_target', query)
    await bot.run_bot_notifications(session, now)
    current = {**current, 'id': 11, 'text': '活动10月15日截止。', 'published_at': now + timedelta(minutes=1)}
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    buffer = await bot.load_merge_buffer(session, row.id)
    assert len(buffer['items']) == 1 and '10月15日' in buffer['items'][0]['text']
    await bot.run_bot_notifications(session, now + timedelta(minutes=6))
    rows = list(await session.scalars(select(BotDelivery)))
    assert len(rows) == 1 and '10月12日' not in rows[0].body and '10月15日' in rows[0].body
    assert not rows[0].brief['items'][0]['is_followup']


async def test_followup_remembers_earlier_reward_after_a_date_only_notice(session):
    from app.services.bot_briefs import fallback_items

    now = utcnow().replace(hour=4, minute=0, second=0, microsecond=0)
    row = await target(session, now)
    first = {'id': 1, 'event_id': 42, 'community': 'wuwa', 'title': '测试活动',
             'text': '活动10月12日截止。奖励包含500星声。', 'published_at': now - timedelta(hours=2)}
    second = {**first, 'id': 2, 'text': '活动10月15日截止。', 'published_at': now - timedelta(hours=1)}
    for item in [first, second]:
        delivery = await bot.create_delivery(session, row, 'updates', '历史消息', f'history-{item["id"]}', brief=bot.single_item_brief(item))
        delivery.status, delivery.updated_at = 'accepted', item['published_at']
    await session.commit()
    new = {**first, 'id': 3, 'text': '活动10月15日截止。奖励包含500星声。', 'published_at': now}
    prepared = await bot.prepare_update_items(session, row.id, [new], now, set())
    assert '奖励包含500星声。' in prepared[0]['previous_points']
    assert fallback_items(prepared) == []
