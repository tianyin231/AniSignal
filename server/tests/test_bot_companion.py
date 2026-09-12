from datetime import timedelta, timezone

from sqlalchemy import select

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotDelivery, BotReminder, BotTarget
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
