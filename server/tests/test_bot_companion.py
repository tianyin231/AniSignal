from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotDelivery, BotReminder, BotTarget, ContentSchedule, RawContent, RedeemCode
from app.services import bot
from app.services.settings import set_setting

HEADERS = {"X-Bot-Key": "plugin-key"}


async def setup(session, **config):
    await set_setting(session, "bot_bridge", {"tools_key": "plugin-key"})
    body = TargetBody(name="测试群", umo="qq:GroupMessage:group", enabled=True,
        daily_enabled=False, delivery_mode="astrbot", chat_controls=True, reminders_enabled=True,
        manager_ids=["owner"], **config)
    row = BotTarget(name=body.name, umo=body.umo, enabled=True,
        config=body.model_dump(exclude={"name", "umo", "enabled"}))
    session.add(row)
    await session.commit()
    return row


async def test_group_settings_require_authorized_actor(client, session):
    row = await setup(session)
    body = {"umo": row.umo, "actor_id": "member", "action": "keywords", "value": "键盘"}
    assert (await client.post('/api/bot/subscription', json=body)).status_code == 401
    assert (await client.post('/api/bot/subscription', headers=HEADERS, json=body)).status_code == 403
    body['actor_id'] = 'owner'
    response = await client.post('/api/bot/subscription', headers=HEADERS, json=body)
    assert response.status_code == 200 and response.json()['keywords'] == ['键盘']
    body.update(action='daily', value='99:99')
    assert (await client.post('/api/bot/subscription', headers=HEADERS, json=body)).status_code == 422
    await session.refresh(row)
    assert row.config['daily_enabled'] is False


async def test_plugin_queue_claim_receipt_and_context_isolation(client, session, monkeypatch):
    row = await setup(session)
    delivery = await bot.create_delivery(session, row, 'manual', '原始资料', 'unique')
    async def no_direct_send(*args):
        raise AssertionError('plugin delivery must not go through the plain gateway')
    monkeypatch.setattr(bot, 'send_message', no_direct_send)
    await bot.deliver(session, row, delivery)
    claimed = (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={'version':'1.1.0'})).json()['delivery']
    assert claimed['id'] == delivery.id
    assert (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery'] is None
    path = f'/api/bot/outbox/{delivery.id}/complete'
    body = {'receipt':'wrong', 'status':'accepted', 'rendered_body':'人格播报'}
    assert (await client.post(path, headers=HEADERS, json=body)).status_code == 409
    body['receipt'] = claimed['receipt']
    assert (await client.post(path, headers=HEADERS, json=body)).status_code == 200
    assert (await client.post(path, headers=HEADERS, json=body)).status_code == 200
    context = await client.post('/api/bot/context', headers=HEADERS, json={'umo':row.umo,'actor_id':'member'})
    assert context.json()['recent_notifications'][0]['text'] == '人格播报'
    assert (await client.post('/api/bot/context', headers=HEADERS, json={'umo':'qq:GroupMessage:other','actor_id':'member'})).status_code == 403


async def test_notification_context_keeps_publish_time_separate_from_sent_time(client, session):
    row = await setup(session)
    delivery = await bot.create_delivery(session, row, 'updates', '消息正文', 'publication-context', brief={'items': [
        {'title': '早些发布的动态', 'community': 'wuwa', 'published_at': '2026-10-09T06:00:00',
         'sources': [{'published_at': '2026-10-09T06:00:00'}]},
        {'title': '未知发布时间', 'community': 'wuwa', 'published_at': None},
    ]})
    delivery.status = 'accepted'
    await session.commit()
    response = await client.post('/api/bot/context', headers=HEADERS, json={'umo': row.umo, 'actor_id': 'member'})
    notification = response.json()['recent_notifications'][0]
    items = notification['brief']['items']
    assert notification['sent_at'] != items[0]['published_at']
    assert items[0]['published_at'] == '2026-10-09T06:00:00'
    assert items[0]['published_label'] == '发布：10/09 14:00'
    assert items[1]['published_at'] is None and items[1]['published_label'] == ''


async def test_reminder_idempotency_owner_cancel_and_time(client, session):
    row = await setup(session)
    due = (utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc)
    body = {'umo':row.umo, 'actor_id':'member', 'action':'create', 'text':'看公告', 'due_at':due.isoformat(), 'request_id':'m1'}
    first = await client.post('/api/bot/reminders', headers=HEADERS, json=body)
    assert first.status_code == 200
    rid = first.json()['reminder']['id']
    second = await client.post('/api/bot/reminders', headers=HEADERS, json=body)
    assert second.json()['reminder']['id'] == rid and second.json()['reused']
    stored = await session.get(BotReminder, rid)
    assert stored.due_at == due.replace(tzinfo=None)
    body.update(action='cancel', reminder_id=rid, actor_id='another')
    assert (await client.post('/api/bot/reminders', headers=HEADERS, json=body)).status_code == 404
    body['actor_id'] = 'member'
    assert (await client.post('/api/bot/reminders', headers=HEADERS, json=body)).status_code == 200
    await bot.run_bot_notifications(session, due.replace(tzinfo=None) + timedelta(minutes=1))
    assert not list(await session.scalars(select(BotDelivery)))


async def test_due_reminder_survives_subscription_edit_and_cancel_queue(client, session, admin_headers):
    row = await setup(session)
    reminder = BotReminder(target_id=row.id, owner_id='member', request_key='r1', body='开活动', due_at=utcnow()-timedelta(seconds=1))
    session.add(reminder)
    await session.commit()
    await bot.run_bot_notifications(session)
    await bot.run_bot_notifications(session)
    rows = list(await session.scalars(select(BotDelivery)))
    assert len(rows) == 1 and rows[0].status == 'pending'
    await client.post('/api/bot/subscription', headers=HEADERS, json={'umo':row.umo,'actor_id':'owner','action':'keywords','value':'角色'})
    assert len(list(await session.scalars(select(BotDelivery)))) == 1
    response = await client.post(f'/api/admin/bot/reminders/{reminder.id}/cancel', headers=admin_headers)
    assert response.status_code == 200
    assert (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery'] is None


async def test_unknown_does_not_reenter_outbox_and_old_receipt_fails(client, session, admin_headers):
    row = await setup(session)
    delivery = await bot.create_delivery(session, row, 'manual', 'data', 'manual1')
    claimed = (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery']
    body = {'receipt':claimed['receipt'], 'status':'unknown'}
    await client.post(f'/api/bot/outbox/{delivery.id}/complete', headers=HEADERS, json=body)
    assert (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery'] is None
    await client.post(f'/api/admin/bot/deliveries/{delivery.id}/retry', headers=admin_headers)
    second = (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery']
    assert second['receipt'] != claimed['receipt']
    assert (await client.post(f'/api/bot/outbox/{delivery.id}/complete', headers=HEADERS, json=body)).status_code == 409


async def test_disabled_features_and_reminder_limit(client, session):
    row = await setup(session)
    for n in range(10):
        session.add(BotReminder(target_id=row.id, owner_id='member', request_key=str(n), body='提醒', due_at=utcnow()+timedelta(days=1)))
    await session.commit()
    body = {'umo':row.umo,'actor_id':'member','action':'create','text':'新提醒','due_at':(utcnow()+timedelta(days=2)).isoformat()+'Z','request_id':'new'}
    assert (await client.post('/api/bot/reminders', headers=HEADERS, json=body)).status_code == 409
    row.enabled = False
    await session.commit()
    assert (await client.post('/api/bot/reminders', headers=HEADERS, json=body)).status_code == 403


async def test_targets_listing_includes_paused_sessions_with_names(client, session):
    row = await setup(session)
    row.enabled = False
    paused = BotTarget(name='私聊', umo='qq:FriendMessage:alice', enabled=False, config={})
    session.add(paused)
    await session.commit()
    rows = (await client.get('/api/bot/targets', headers=HEADERS)).json()
    by_umo = {r['umo']: r for r in rows}
    assert by_umo[row.umo]['name'] == '测试群' and by_umo[row.umo]['enabled'] is False
    assert by_umo[paused.umo]['name'] == '私聊' and by_umo[paused.umo]['enabled'] is False


async def test_activity_report_keeps_latest_and_ignores_unknown(client, session, admin_headers):
    row = await setup(session)
    head = {'X-Bot-Key': 'plugin-key'}
    body = {'activities': {row.umo: '2026-09-12T02:00:00+00:00', 'qq:GroupMessage:ghost': '2026-09-12T03:00:00+00:00'}}
    assert (await client.post('/api/bot/activity', headers=head, json=body)).status_code == 200
    overview = (await client.get('/api/admin/bot', headers=admin_headers)).json()
    assert overview['activity'] == {row.umo: '2026-09-12T02:00:00'}
    older = {'activities': {row.umo: '2026-09-11T00:00:00+00:00'}}
    await client.post('/api/bot/activity', headers=head, json=older)
    overview = (await client.get('/api/admin/bot', headers=admin_headers)).json()
    assert overview['activity'][row.umo] == '2026-09-12T02:00:00'
    newer = {'activities': {row.umo: '2026-09-12T05:30:00+00:00'}}
    await client.post('/api/bot/activity', headers=head, json=newer)
    overview = (await client.get('/api/admin/bot', headers=admin_headers)).json()
    assert overview['activity'][row.umo] == '2026-09-12T05:30:00'


async def test_calendar_reminders_and_query_tools(client, session, monkeypatch):
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr('app.api.bot_companion.utcnow', lambda: now)
    monkeypatch.setattr(bot, 'utcnow', lambda: now)
    await set_setting(session, 'bot_bridge', {'tools_key': 'plugin-key'})
    body = TargetBody(name='日历群', umo='qq:GroupMessage:cal', enabled=True, daily_enabled=False,
        updates_enabled=True, delivery_mode='astrbot', community='wuwa')
    row = BotTarget(name=body.name, umo=body.umo, enabled=True,
        config=body.model_dump(exclude={'name', 'umo', 'enabled'}))
    session.add(row)
    content = RawContent(platform='kuro_cn', content_type='notice', external_id='cal-1',
        community='wuwa', url='https://example.com/post')
    session.add(content)
    await session.commit()
    session.add(ContentSchedule(raw_content_id=content.id, community='wuwa', title='限时活动「回响」',
        kind='activity', start_at=utcnow() - timedelta(days=3),
        end_at=now + timedelta(hours=1, minutes=30), created_at=now))
    session.add(RedeemCode(raw_content_id=content.id, community='wuwa', code='CAL2026',
        status='active', created_at=utcnow()))
    await session.commit()

    await bot.run_bot_notifications(session, now)
    deliveries = list(await session.scalars(select(BotDelivery)))
    assert len(deliveries) == 1 and deliveries[0].kind == 'calendar'
    assert '回响' in deliveries[0].body
    assert deliveries[0].brief['items'][0]['calendar_entries'][0]['title'] == '限时活动「回响」'
    dedupe_key = deliveries[0].dedupe_key
    assert dedupe_key.startswith('calendar-digest:')

    # 同一时刻重复扫描去重
    await bot.run_bot_notifications(session, now)
    assert len(list(await session.scalars(select(BotDelivery)))) == 1

    calendar = (await client.post('/api/bot/query', headers=HEADERS,
        json={'umo': row.umo, 'tool': 'calendar'})).json()
    assert '限时活动「回响」' in calendar['text']
    codes = (await client.post('/api/bot/query', headers=HEADERS,
        json={'umo': row.umo, 'tool': 'codes'})).json()
    assert 'CAL2026' in codes['text']


@pytest.mark.parametrize('max_images', [1, 3])
async def test_daily_digest_carries_cover_and_claim_returns_images(client, session, monkeypatch, max_images):
    now = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr('app.api.bot_companion.utcnow', lambda: now)
    await set_setting(session, 'bot_bridge', {'tools_key': 'plugin-key'})
    body = TargetBody(name='图群', umo='qq:GroupMessage:media', enabled=True,
        daily_enabled=True, daily_time='08:00', delivery_mode='astrbot', max_images=max_images)
    row = BotTarget(name=body.name, umo=body.umo, enabled=True,
        config=body.model_dump(exclude={'name', 'umo', 'enabled'}))
    row.created_at = utcnow().replace(hour=0, minute=0) - timedelta(days=1)
    session.add(row)
    await session.commit()

    async def fake_query(session, target, tool='digest', query='', now=None, collected_after=None):
        return {'text': '今日速报', 'items': [{'id': i, 'title': f'官方新动态{i}', 'image': f'https://img/{i}.jpg', 'url': f'https://x/{i}', 'identity': 'official'} for i in range(7, 10)],
                'instructions': '', 'grounding': ''}

    monkeypatch.setattr(bot, 'query_target', fake_query)
    await bot.run_bot_notifications(session, now)
    delivery = (await session.execute(select(bot.BotDelivery))).scalar_one()
    assert delivery.media == [f'https://img/{i}.jpg' for i in range(7, 7 + max_images)]

    claimed = (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery']
    assert claimed['images'] == delivery.media
    assert claimed['max_images'] == max_images


async def test_brief_detail_is_bound_to_original_delivery_and_target(client, session):
    row = await setup(session, tools=['news'])
    raw = RawContent(platform='bilibili', content_type='notice', external_id='brief-original', community='wuwa')
    session.add(raw)
    await session.commit()
    brief = {'items': [{'title': '原来的第二条', 'summary': '已发布', 'sources': [
        {'content_ids': [raw.id], 'title': '原公告', 'url': 'https://example.com/original'}]}]}
    delivery = await bot.create_delivery(session, row, 'updates', '简报', 'brief-snapshot', brief=brief)
    delivery.status = 'accepted'
    other = BotTarget(name='另一群', umo='qq:GroupMessage:another', enabled=True, config={'tools': ['news']})
    session.add(other)
    await session.commit()
    body = {'umo': row.umo, 'actor_id': 'member', 'number': 1, 'delivery_id': delivery.id}
    result = await client.post('/api/bot/detail', headers=HEADERS, json=body)
    assert result.status_code == 200 and 'https://example.com/original' in result.json()['text']
    assert (await client.post('/api/bot/detail', headers=HEADERS, json={**body, 'umo': other.umo})).status_code == 404
    raw.hidden = True
    await session.commit()
    assert (await client.post('/api/bot/detail', headers=HEADERS, json=body)).status_code == 404


async def test_claim_cancels_empty_dynamic_and_rebuilds_mixed_brief(client, session):
    row = await setup(session, updates_enabled=True, quiet_start=0, quiet_end=0, max_images=2)
    empty = {'id': 1, 'title': '空动态不应出现', 'summary': '虚有摘要', 'text': '',
             'platform': 'bilibili', 'content_type': 'dynamic', 'url': 'https://t.bilibili.com/1'}
    cancelled = await bot.create_delivery(session, row, 'updates', '旧空动态正文', 'empty-claim',
                                          brief=bot.single_item_brief(empty))
    image_only = {'id': 2, 'title': '纯图片公告', 'text': '', 'image': 'https://img.example/2.jpg',
                  'platform': 'bilibili', 'content_type': 'dynamic', 'url': 'https://t.bilibili.com/2',
                  'published_at': utcnow().isoformat()}
    mixed = await bot.create_delivery(session, row, 'updates', '空动态不应出现\n纯图片公告', 'mixed-claim',
                                      brief={'mode': 'updates', 'title': '刚刚的重点', 'date': '09/22',
                                             'items': [empty, image_only, {**image_only, 'id': 3, 'image': 'https://img.example/3.jpg'}]})
    claimed = (await client.post('/api/bot/outbox/claim', headers=HEADERS, json={})).json()['delivery']
    assert claimed['id'] == mixed.id
    assert '空动态不应出现' not in claimed['body'] and '纯图片公告' in claimed['body']
    assert claimed['images'] == ['https://img.example/2.jpg', 'https://img.example/3.jpg']
    assert [item['id'] for item in claimed['brief']['items']] == [2, 3]
    await session.refresh(cancelled)
    assert cancelled.status == 'cancelled' and cancelled.attempts == 0
