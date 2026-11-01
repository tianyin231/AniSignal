"""用户插件掌握订阅；鉴权隔离实例和操作者，排除词贯穿通知链路。"""

from datetime import timedelta

import pytest

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotTarget, ContentSchedule, ContentVersion, RawContent, Source
from app.services import bot
from app.services.bot_access import matches_keywords
from app.services.bot_calendar import calendar_brief

UMO = 'qq:GroupMessage:allowed'
CHANNEL = 'wuwa:official:youtube'


async def access(client, admin_headers):
    response = await client.post('/api/admin/bot/clients', headers=admin_headers, json={'name': '用户接入'})
    data = response.json()
    return data['id'], {'X-Bot-Key': data['key']}


def settings(umo=UMO, **overrides):
    return TargetBody(name='订阅', umo=umo, enabled=True, channels=[CHANNEL], daily_enabled=False,
        updates_enabled=True, delivery_mode='astrbot', chat_controls=True, manager_ids=['owner'], **overrides).model_dump()


async def sync(client, headers, targets):
    return await client.post('/api/bot/configuration', headers=headers, json={'targets': targets})


async def test_user_plugin_controls_recipients_and_sources_without_server_approval(client, admin_headers):
    client_id, headers = await access(client, admin_headers)
    own = settings(umo='qq:GroupMessage:user-selected', custom_feeds=['nikke_x'])
    assert (await sync(client, headers, [own])).status_code == 200
    assert (await client.put(f'/api/admin/bot/clients/{client_id}/policy', headers=admin_headers, json={})).status_code == 404
    assert (await client.post('/api/bot/query', headers=headers, json={'umo': own['umo'], 'tool': 'news'})).status_code == 403
    assert (await client.post('/api/bot/query', headers=headers, json={'umo': own['umo'], 'actor_id': 'member', 'tool': 'news'})).status_code == 200


async def test_group_filter_is_shared_and_only_user_authorized_managers_can_change_it(client, admin_headers):
    _, headers = await access(client, admin_headers)
    await sync(client, headers, [settings()])
    payload = {'umo': UMO, 'actor_id': 'member', 'action': 'exclude_keywords', 'value': '抽奖'}
    assert (await client.post('/api/bot/subscription', headers=headers, json=payload)).status_code == 403
    payload['actor_id'] = 'owner'
    response = await client.post('/api/bot/subscription', headers=headers, json=payload)
    assert response.status_code == 200 and response.json()['exclude_keywords'] == ['抽奖']
    payload.update(action='community', value='全站')
    response = await client.post('/api/bot/subscription', headers=headers, json=payload)
    assert response.status_code == 200 and response.json()['channels'] == []
    assert response.json()['exclude_keywords'] == ['抽奖']


@pytest.mark.parametrize('path,payload', [
    ('/subscription', {}), ('/context', {}), ('/detail', {}),
    ('/reminders', {}), ('/query', {'tool': 'news'}),
])
async def test_private_session_identity_is_not_just_an_arbitrary_actor(client, admin_headers, path, payload):
    _, headers = await access(client, admin_headers)
    umo = 'qq:FriendMessage:alice'
    await sync(client, headers, [settings(umo=umo)])
    response = await client.post('/api/bot'+path, headers=headers, json={'umo': umo, 'actor_id': 'bob', **payload})
    assert response.status_code == 403 and '自己的私聊' in response.json()['detail']
    assert (await client.post('/api/bot/subscription', headers=headers, json={'umo': umo, 'actor_id': 'alice'})).status_code == 200


async def test_revoked_instance_cannot_send_through_a_stale_worker(client, admin_headers, session, monkeypatch):
    client_id, headers = await access(client, admin_headers)
    result = (await sync(client, headers, [settings()])).json()['targets'][0]
    target = await session.get(BotTarget, result['id'])
    row = await bot.create_delivery(session, target, 'manual', '待发送', 'disabled-instance')
    response = await client.put(f'/api/admin/bot/clients/{client_id}', headers=admin_headers,
        json={'name': '用户接入', 'enabled': False})
    assert response.status_code == 200
    assert (await client.get('/api/bot/targets', headers=headers)).status_code == 401
    late = await bot.create_delivery(session, target, 'manual', '旧进程消息', 'stale-worker')
    target.enabled = True
    target.config = {**target.config, 'delivery_mode': 'direct'}
    await session.commit()
    async def no_send(*args):
        raise AssertionError('已撤销实例不能进入网关')
    monkeypatch.setattr(bot, 'send_message', no_send)
    await bot.deliver(session, target, late, force=True)
    assert late.status == 'cancelled'


def test_keyword_exclusion_wins_and_checks_source_text():
    config = {'keywords': ['键盘'], 'exclude_keywords': ['抽奖', 'ADVERTISEMENT']}
    assert matches_keywords(config, {'title': '键盘发货'})
    assert not matches_keywords(config, {'title': '键盘发布', 'sources': [{'text': '抽奖活动'}]})
    assert not matches_keywords(config, {'title': '键盘 advertisement'})
    assert not matches_keywords(config, {'title': '角色更新'})


async def test_filter_runs_before_grouping_and_rechecks_queued_updates(session):
    now = utcnow()
    source = Source(platform='youtube', external_id='official', identity='official')
    session.add(source)
    await session.flush()
    for index, title in enumerate(['键盘发货公告', '键盘抽奖活动']):
        raw = RawContent(platform='youtube', content_type='video', external_id=str(index), source_id=source.id,
            community='wuwa', published_at=now-timedelta(minutes=2), collected_at=now-timedelta(minutes=2), url=f'https://example.com/{index}')
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, version=1, parser_version='1', title=title, text=title, category='version_update', normalized_json={}))
    body = settings(keywords=['键盘'], exclude_keywords=['抽奖'])
    target = BotTarget(name=body['name'], umo=UMO, enabled=True, config={k:v for k,v in body.items() if k not in {'name','umo','enabled'}})
    session.add(target)
    await session.commit()
    payload = await bot.query_target(session, target, collected_after=now-timedelta(minutes=10), now=now)
    assert [item['title'] for item in payload['items']] == ['键盘发货公告']
    brief = bot.single_item_brief(payload['items'][0])
    delivery = await bot.create_delivery(session, target, 'updates', '旧正文', 'filter-queue', brief=brief)
    target.config = {**target.config, 'exclude_keywords': ['发货']}
    await session.commit()
    assert not await bot.prepare_delivery(session, delivery, target=target, now=now)
    assert delivery.status == 'cancelled'


async def test_queued_calendar_rechecks_community_and_keyword_filter(session):
    now = utcnow().replace(hour=12, minute=0)
    raw = RawContent(platform='youtube', content_type='video', external_id='calendar-filter', community='wuwa', url='https://example.com')
    session.add(raw)
    await session.flush()
    schedule = ContentSchedule(raw_content_id=raw.id, community='wuwa', title='抽奖活动', kind='activity', all_day=False,
        start_at=now-timedelta(days=1), end_at=now+timedelta(hours=2))
    session.add(schedule)
    await session.flush()
    entry = {'title': schedule.title, 'kind': 'activity', 'community': 'wuwa', 'stage_at': schedule.end_at.isoformat(),
        'expires_at': schedule.end_at.isoformat(), 'url': raw.url, 'schedule_ids': [schedule.id], 'content_ids': [raw.id]}
    text, brief = calendar_brief([entry], now)
    target = BotTarget(name='日程', umo=UMO, enabled=True, config={'updates_enabled': True, 'exclude_keywords': ['抽奖']})
    session.add(target)
    await session.commit()
    row = await bot.create_delivery(session, target, 'calendar', text, 'calendar-filter', brief=brief)
    assert not await bot.prepare_delivery(session, row, target=target, now=now)
    assert row.status == 'cancelled'
