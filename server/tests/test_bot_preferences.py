"""按账号加载来源、独立筛选通知，并在发送前重新验证权限和个人设置。"""

import copy
from datetime import timedelta

import pytest

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentSchedule, CustomFeed, CustomFeedItem, UserKeyword
from sqlalchemy import select
from app.services import bot, bot_custom
from app.services.bot_preferences import resolve_target
from tests.test_bot_channels import content
from tests.test_user_bot_tokens import account, issue


async def connect(client, session, **config):
    user, cookie = await account(session)
    data, key = await issue(client, cookie)
    body = TargetBody(name='按账号订阅', umo='qq:GroupMessage:customized', enabled=True,
        daily_enabled=False, updates_enabled=True, delivery_mode='astrbot', **config).model_dump()
    response = await client.post('/api/bot/configuration', headers=key, json={'targets': [body]})
    assert response.status_code == 200, response.text
    row = await session.get(BotTarget, response.json()['targets'][0]['id'])
    return user, key, row


async def feed(session, user_id, name, public=False):
    row = CustomFeed(user_id=user_id, name=name, platform='bilibili', kind='uid', target_value='123', is_public=public)
    session.add(row)
    await session.flush()
    session.add(CustomFeedItem(feed_id=row.id, platform='bilibili', external_id=name,
        title=name, url='https://example.com/'+name, published_at=utcnow()-timedelta(minutes=5)))
    await session.commit()
    return row


async def test_catalog_and_configuration_only_allow_public_or_owned_monitors(client, session):
    user, key, target = await connect(client, session)
    other, _ = await account(session, 'other@example.com')
    mine = await feed(session, user.id, '本人私有')
    private = await feed(session, other.id, '别人的私有')
    public = await feed(session, other.id, '公共监控', True)
    options = (await client.get('/api/bot/catalog', headers=key)).json()
    assert {f['id'] for f in options['custom_feeds']} == {mine.id, public.id}
    assert '别人的私有' not in str(options)
    current = (await client.get('/api/bot/configuration', headers=key)).json()['targets'][0]
    for extra in [{'custom_feed_ids': [private.id]}, {'daily_sources': [f'feed:{private.id}']}]:
        denied = await client.post('/api/bot/configuration', headers=key, json={'targets': [{**current, **extra}]})
        assert denied.status_code == 403
    assert (await client.get('/api/bot/configuration', headers=key)).json()['targets'][0]['custom_feed_ids'] == []


async def test_daily_and_updates_choose_separate_platforms_without_expanding_base_scope(client, session):
    await content(session, 'B站更新')
    await content(session, 'YouTube更新', 'youtube')
    await content(session, 'X更新', 'x')
    _, _, target = await connect(client, session, channels=['wuwa:official:bilibili', 'wuwa:official:youtube'],
        daily_sources=['wuwa:official:bilibili'], updates_sources=['wuwa:official:youtube', 'wuwa:official:x'])
    start = utcnow()-timedelta(hours=1)
    daily = await bot.query_target(session, target, collected_after=start, notification='daily')
    updates = await bot.query_target(session, target, collected_after=start, notification='updates')
    assert {i['platform'] for i in daily['items']} == {'bilibili'}
    assert {i['platform'] for i in updates['items']} == {'youtube'}
    assert {i['platform'] for i in (await bot.query_target(session, target, collected_after=start))['items']} == {'bilibili', 'youtube'}
    row = await bot.create_delivery(session, target, 'updates', updates['text'], 'filtered-update', brief=bot.single_item_brief(updates['items'][0]))
    assert await bot.prepare_delivery(session, row), row.error
    target.config = {**target.config, 'updates_sources': ['wuwa:official:bilibili']}
    await session.commit()
    assert not await bot.prepare_delivery(session, row)


async def test_account_preferences_apply_live_and_do_not_mutate_plugin_choices(client, session):
    await content(session, '保留角色')
    await content(session, '抽奖角色')
    user, _, target = await connect(client, session, communities=['wuwa', 'blhx'], inherit_preferences=True, inherit_keywords=True)
    user.feed_preferences = {'communities': ['wuwa'], 'exclude_keywords': ['抽奖']}
    session.add(UserKeyword(user_id=user.id, keyword='角色', enabled=True))
    await session.commit()
    original = copy.deepcopy(target.config)
    payload = await bot.query_target(session, target, collected_after=utcnow()-timedelta(hours=1))
    assert [i['title'] for i in payload['items']] == ['保留角色']
    row = await bot.create_delivery(session, target, 'updates', payload['text'], 'account-preferences', brief=bot.single_item_brief(payload['items'][0]))
    assert await bot.prepare_delivery(session, row), row.error
    user.feed_preferences = {'communities': ['blhx'], 'exclude_keywords': []}
    await session.commit()
    assert not await bot.prepare_delivery(session, row)
    assert target.config == original


async def test_importing_existing_channel_settings_preserves_cursor_and_history(client, session):
    _, key, target = await connect(client, session, channels=['wuwa:official:youtube'])
    before = utcnow()-timedelta(hours=2)
    old = {k: v for k, v in target.config.items() if k not in {
        'scope_mode', 'inherit_preferences', 'inherit_keywords', 'daily_sources', 'updates_sources', 'custom_feed_ids', 'calendar_kinds'}}
    target.config, target.last_scan_at = old, before
    session.add(BotDelivery(target_id=target.id, kind='manual', body='保留历史', dedupe_key='before-upgrade'))
    await session.commit()
    exported = (await client.get('/api/bot/configuration', headers=key)).json()['targets']
    assert (await client.post('/api/bot/configuration', headers=key, json={'targets': exported})).status_code == 200
    await session.refresh(target)
    assert target.last_scan_at == before
    assert await session.scalar(select(BotDelivery).where(BotDelivery.target_id == target.id))


@pytest.mark.parametrize('mode', ['channels', 'custom'])
async def test_empty_explicit_scope_never_falls_back_to_all_platforms(client, session, mode):
    await content(session, '不应收到')
    _, _, target = await connect(client, session, scope_mode=mode, channels=[])
    assert not (await bot.query_target(session, target, collected_after=utcnow()-timedelta(hours=1)))['items']
    assert bot.subscribed_communities(await resolve_target(session, target)) == []


async def test_personal_private_monitor_can_push_but_permission_is_rechecked_before_send(client, session):
    user, key, target = await connect(client, session, scope_mode='custom')
    mine = await feed(session, user.id, '私人监控更新')
    settings = (await client.get('/api/bot/configuration', headers=key)).json()['targets'][0]
    settings.update(custom_feed_ids=[mine.id])
    response = await client.post('/api/bot/configuration', headers=key, json={'targets': [settings]})
    assert response.status_code == 200
    await session.refresh(target)
    payload = await bot.query_target(session, target, collected_after=utcnow()-timedelta(hours=1))
    assert [i['title'] for i in payload['items']] == ['私人监控更新']
    item = payload['items'][0]
    assert await bot_custom.valid_item(session, item, target)
    row = await bot.create_delivery(session, target, 'updates', payload['text'], 'private-custom', brief=bot.single_item_brief(item))
    assert await bot.prepare_delivery(session, row), row.error
    row.status = 'accepted'
    await session.commit()
    mine.enabled = False
    await session.commit()
    actor = {'umo': target.umo, 'actor_id': 'member'}
    assert not (await client.post('/api/bot/context', headers=key, json=actor)).json()['recent_notifications']
    assert (await client.post('/api/bot/detail', headers=key, json={**actor, 'delivery_id': row.id})).status_code == 404
    row.status = 'pending'
    assert not await bot.prepare_delivery(session, row)
    assert row.status == 'cancelled'


async def test_calendar_kind_selection_applies_to_query_queue_and_send(client, session):
    now = utcnow()
    raw = await content(session, '日程公告')
    for kind in ['activity', 'pool', 'maintenance', 'deadline']:
        session.add(ContentSchedule(raw_content_id=raw.id, community='wuwa', title=kind+'日程', kind=kind,
            all_day=False, start_at=now+(timedelta(hours=1) if kind in {'maintenance', 'deadline'} else -timedelta(days=1)),
            end_at=now+timedelta(hours=1)))
    await session.commit()
    _, _, target = await connect(client, session, communities=['wuwa'], calendar_enabled=True,
        calendar_kinds=['maintenance'], calendar_time=(now+timedelta(hours=8)).strftime('%H:%M'), quiet_start=0, quiet_end=0)
    payload = await bot.query_target(session, target, 'calendar', now=now)
    assert 'maintenance日程' in payload['text'] and 'activity日程' not in payload['text']
    await bot.run_bot_notifications(session, now=now)
    row = await session.scalar(select(BotDelivery).where(BotDelivery.kind == 'calendar', BotDelivery.target_id == target.id))
    assert row and {e['kind'] for e in row.brief['items'][0]['calendar_entries']} == {'maintenance'}
    target.config = {**target.config, 'calendar_kinds': []}
    await session.commit()
    assert not await bot.prepare_delivery(session, row, now=now)
