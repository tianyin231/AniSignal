"""定制官号复用通知管线；不调用真实消息平台。"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api.bot import TargetBody
from app.clock import utcnow
from app.custom_presets import CUSTOM_PRESETS
from app.models import BotDelivery, BotTarget, CustomFeed, CustomFeedItem, CustomFeedSource, CustomSourceItem, RawContent
from app.services import bot, bot_briefs


async def seed(session, key, *, age=1, public=True):
    preset = CUSTOM_PRESETS[key]
    source = CustomFeedSource(platform=preset['platform'], kind=preset['kind'], target_value=preset['target_value'])
    session.add(source); await session.flush()
    feed = CustomFeed(source_id=source.id, name=preset['name'], platform=source.platform,
        kind=source.kind, target_value=source.target_value, is_public=public)
    session.add(feed); await session.flush()
    now = utcnow()
    session.add(CustomFeedItem(feed_id=feed.id, platform=source.platform, external_id='notice-1',
        title='NIKKE 4th Anniversary Rewards', url='https://example.com/notice-1',
        published_at=now-timedelta(hours=age), collected_at=now-timedelta(minutes=2)))
    session.add(CustomSourceItem(source_id=source.id, external_id='notice-1', normalized={
        'text':'Commanders can claim anniversary rewards. See the official notice for conditions.'}))
    await session.commit()
    return feed


async def target(session, keys):
    body = TargetBody(name='测试群', umo='mock:GroupMessage:nikke', enabled=True,
        communities=['wuwa'], custom_feeds=keys, updates_enabled=True, daily_enabled=False,
        calendar_enabled=False, quiet_start=0, quiet_end=0, delivery_mode='astrbot')
    row = BotTarget(name=body.name, umo=body.umo, enabled=True,
        config=body.model_dump(exclude={'name','umo','enabled'}), last_scan_at=utcnow()-timedelta(minutes=5))
    session.add(row); await session.commit()
    return row


@pytest.mark.parametrize('key',list(CUSTOM_PRESETS))
async def test_custom_sources_use_existing_outbox_and_keep_original_time_without_model(session,monkeypatch,key):
    feed=await seed(session,key)
    row=await target(session,[key])
    async def no_model(*args,**kwargs):
        raise AssertionError('定制官号不应额外调用摘要模型')
    monkeypatch.setattr(bot_briefs,'select_brief_items',no_model)
    await bot.run_bot_notifications(session)
    delivery=await session.scalar(select(BotDelivery))
    assert delivery and delivery.target_id==row.id and delivery.kind=='updates'
    item=delivery.brief['items'][0]
    assert item['custom_feed']=={'preset':key,'feed_id':feed.id}
    assert item['platform']==feed.platform and item['url']=='https://example.com/notice-1'
    assert item['published_at'] and item['sources'][0]['custom_feed']==item['custom_feed']
    assert await bot.prepare_delivery(session,delivery)
    assert await session.scalar(select(func.count(RawContent.id)))==0
    # 可能已经送达的 unknown 记录保留，下一轮不会重建或自动重试。
    delivery.status='unknown'; await session.commit()
    await bot.run_bot_notifications(session)
    assert await session.scalar(select(func.count(BotDelivery.id)))==1
    assert delivery.status=='unknown'
    feed.enabled=False;await session.commit()
    assert not await bot.prepare_delivery(session,delivery)


async def test_unsubscribed_private_stale_and_pre_subscription_items_are_not_updates(session):
    feed=await seed(session,'nikke_x',public=False)
    await seed(session,'nikke_youtube',age=48)
    await seed(session,'nikke_global')
    row=await target(session,['nikke_x','nikke_youtube'])
    payload=await bot.query_target(session,row,collected_after=row.last_scan_at)
    assert not payload['items']
    feed.is_public=True;await session.commit()
    row.last_scan_at=utcnow();await session.commit()
    assert not (await bot.query_target(session,row,collected_after=row.last_scan_at))['items']


async def test_add_custom_subscription_keeps_existing_scope_and_can_be_removed_before_send(session):
    await seed(session,'nikke_x')
    row=await target(session,[])
    assert bot.subscribed_communities(row)==['wuwa']
    assert not (await bot.query_target(session,row,collected_after=row.last_scan_at))['items']
    row.config={**row.config,'custom_feeds':['nikke_x']};await session.commit()
    await bot.run_bot_notifications(session)
    delivery=await session.scalar(select(BotDelivery))
    assert delivery and bot.subscribed_communities(row)==['wuwa']
    row.config={**row.config,'custom_feeds':[]};await session.commit()
    assert not await bot.prepare_delivery(session,delivery)


def test_custom_source_keys_are_explicit():
    with pytest.raises(ValueError):
        TargetBody(name='测试',umo='mock:GroupMessage:test',custom_feeds=['private-user-feed'])
