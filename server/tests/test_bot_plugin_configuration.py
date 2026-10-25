"""插件配置是唯一来源；不同接入不能互相查看或领取会话消息。"""

from datetime import timedelta

from sqlalchemy import func, select

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotDelivery, BotTarget
from app.services.settings import get_setting, set_setting


async def access(client, admin_headers, name):
    response = await client.post('/api/admin/bot/clients', headers=admin_headers, json={'name': name})
    assert response.status_code == 200
    data = response.json()
    return data['id'], {'X-Bot-Key': data['key']}


def target(name='测试会话', umo='qq:GroupMessage:shared', **settings):
    return TargetBody(name=name, umo=umo, enabled=True, daily_enabled=False,
                      delivery_mode='astrbot', **settings).model_dump()


async def test_instances_can_use_same_umo_without_reading_each_other(client, admin_headers, session):
    a, headers_a = await access(client, admin_headers, '机器人 A')
    b, headers_b = await access(client, admin_headers, '机器人 B')
    for name, headers in [('A 的会话', headers_a), ('B 的会话', headers_b)]:
        response = await client.post('/api/bot/configuration', headers=headers, json={'targets': [target(name)]})
        assert response.status_code == 200
    rows = list(await session.scalars(select(BotTarget).order_by(BotTarget.id)))
    assert len(rows) == 2 and rows[0].umo == rows[1].umo and {r.client_id for r in rows} == {a, b}
    assert [row['name'] for row in (await client.get('/api/bot/targets', headers=headers_b)).json()] == ['B 的会话']
    assert (await client.get('/api/admin/bot', headers={'X-Admin-Token': headers_b['X-Bot-Key']})).status_code == 401
    other = target('A 的私聊', 'telegram:FriendMessage:only-a')
    await client.post('/api/bot/configuration', headers=headers_a, json={'targets': [target(), other]})
    for path, payload in [('/query', {'umo': other['umo'], 'tool': 'products'}),
                          ('/context', {'umo': other['umo'], 'actor_id': 'member'}),
                          ('/subscription', {'umo': other['umo'], 'actor_id': 'member'})]:
        assert (await client.post('/api/bot'+path, headers=headers_b, json=payload)).status_code == 403
    overview = (await client.get('/api/admin/bot', headers=admin_headers)).text
    assert headers_a['X-Bot-Key'] not in overview and headers_b['X-Bot-Key'] not in overview and 'key_hash' not in overview


async def test_claim_and_receipt_are_scoped_to_the_authenticated_instance(client, admin_headers, session):
    _, a = await access(client, admin_headers, 'A')
    _, b = await access(client, admin_headers, 'B')
    row_a = (await client.post('/api/bot/configuration', headers=a, json={'targets': [target('A')]})).json()['targets'][0]
    row_b = (await client.post('/api/bot/configuration', headers=b, json={'targets': [target('B')]})).json()['targets'][0]
    for index, row in enumerate([row_a, row_b]):
        session.add(BotDelivery(target_id=row['id'], kind='manual', body=f'消息{index}', dedupe_key=f'client-message-{index}'))
    await session.commit()
    delivery = (await client.post('/api/bot/outbox/claim', headers=b, json={'version': '1.5.0'})).json()['delivery']
    assert delivery['body'] == '消息1'
    receipt = {'receipt': delivery['receipt'], 'status': 'accepted'}
    assert (await client.post(f"/api/bot/outbox/{delivery['id']}/complete", headers=a, json=receipt)).status_code == 409
    assert (await client.post(f"/api/bot/outbox/{delivery['id']}/complete", headers=b, json=receipt)).status_code == 200
    assert (await client.post('/api/bot/outbox/claim', headers=a, json={})).json()['delivery']['body'] == '消息0'


async def test_import_keeps_identity_history_and_scan_cursor(client, admin_headers, session):
    await set_setting(session, 'bot_bridge', {'tools_key': 'existing-key'})
    body = target(chat_controls=True)
    created = (await client.post('/api/admin/bot/targets', headers=admin_headers, json=body)).json()
    row = await session.get(BotTarget, created['id'])
    before = utcnow() - timedelta(days=1)
    row.last_scan_at = before
    session.add(BotDelivery(target_id=row.id, kind='manual', body='已排队', dedupe_key='keep-pending'))
    session.add(BotDelivery(target_id=row.id, kind='manual', body='已送达', status='accepted', dedupe_key='keep-history'))
    await session.commit()
    headers = {'X-Bot-Key': 'existing-key'}
    exported = (await client.get('/api/bot/configuration', headers=headers)).json()['targets']
    from app.communities import COMMUNITIES
    exported[0]['communities'] = list(COMMUNITIES)
    for _ in range(2):
        response = await client.post('/api/bot/configuration', headers=headers, json={'targets': exported})
        assert response.status_code == 200 and response.json()['targets'][0]['id'] == row.id
    await session.refresh(row)
    assert row.last_scan_at == before and row.managed_by_plugin
    assert await session.scalar(select(func.count()).select_from(BotDelivery)) == 2
    assert (await client.put(f'/api/admin/bot/targets/{row.id}', headers=admin_headers, json=body)).status_code == 409
    assert (await client.delete(f'/api/admin/bot/targets/{row.id}', headers=admin_headers)).status_code == 409


async def test_legacy_persona_and_model_fields_are_retired_without_resetting_delivery_state(client,admin_headers,session):
    _,headers=await access(client,admin_headers,'原生人格')
    settings=target(tools=['news','products'])
    created=(await client.post('/api/bot/configuration',headers=headers,json={'targets':[settings]})).json()['targets'][0]
    row=await session.get(BotTarget,created['id'])
    cursor=utcnow()-timedelta(hours=1)
    row.last_scan_at=cursor
    row.config={**row.config,'ai_prompt':'过期角色指令','ai_provider_id':'old-model'}
    session.add(BotDelivery(target_id=row.id,kind='manual',body='待发送的事实正文',dedupe_key='keep-retired-fields'))
    await session.commit()
    settings.update(ai_prompt='过期角色指令',ai_provider_id='old-model')
    result=await client.post('/api/bot/configuration',headers=headers,json={'targets':[settings]})
    assert result.status_code==200
    await session.refresh(row)
    assert row.last_scan_at==cursor and 'ai_prompt' not in row.config and 'ai_provider_id' not in row.config
    response=result.json()['targets'][0]
    assert not {'ai_prompt','ai_provider_id','instructions','provider_id'}.intersection(response)
    queried=(await client.post('/api/bot/query',headers=headers,json={'umo':row.umo,'tool':'products'})).json()
    assert 'instructions' not in queried and 'provider_id' not in queried
    claimed=(await client.post('/api/bot/outbox/claim',headers=headers,json={'version':'1.8.0'})).json()['delivery']
    assert claimed['body']=='待发送的事实正文'
    assert 'instructions' not in claimed and 'provider_id' not in claimed


async def test_removal_pauses_without_reimporting_or_sending_to_retired_session(client, admin_headers, session):
    _, headers = await access(client, admin_headers, '移除测试')
    paused = target()
    paused['enabled'] = False
    row = (await client.post('/api/bot/configuration', headers=headers, json={'targets': [paused]})).json()['targets'][0]
    session.add(BotDelivery(target_id=row['id'], kind='manual', body='旧会话消息', dedupe_key='retired-pending'))
    session.add(BotDelivery(target_id=row['id'], kind='manual', body='历史记录', status='accepted', dedupe_key='retired-history'))
    await session.commit()
    assert (await client.post('/api/bot/configuration', headers=headers, json={'targets': []})).status_code == 200
    assert (await client.get('/api/bot/configuration', headers=headers)).json()['targets'] == []
    assert (await client.post('/api/bot/outbox/claim', headers=headers, json={})).json()['delivery'] is None
    assert await session.get(BotTarget, row['id'])
    assert await session.scalar(select(func.count()).select_from(BotDelivery)) == 2
    assert (await client.post(f"/api/admin/bot/targets/{row['id']}/send", headers=admin_headers, json={'content': '测试'})).status_code == 409


async def test_disabled_and_rotated_keys_do_not_keep_access(client, admin_headers, session):
    client_id, headers = await access(client, admin_headers, '密钥测试')
    assert (await client.get('/api/bot/targets', headers=headers)).status_code == 200
    changed = await client.post(f'/api/admin/bot/clients/{client_id}/rotate', headers=admin_headers)
    replacement = {'X-Bot-Key': changed.json()['key']}
    assert (await client.get('/api/bot/targets', headers=headers)).status_code == 401
    assert (await client.get('/api/bot/targets', headers=replacement)).status_code == 200
    created = (await client.post('/api/bot/configuration', headers=replacement, json={'targets': [target()]})).json()['targets'][0]
    session.add(BotDelivery(target_id=created['id'], kind='manual', body='等待投递', dedupe_key='disabled-client'))
    await session.commit()
    await client.put(f'/api/admin/bot/clients/{client_id}', headers=admin_headers, json={'name': '密钥测试', 'enabled': False})
    assert (await client.get('/api/bot/targets', headers=replacement)).status_code == 401
    paused = await session.get(BotTarget, created['id'])
    assert not paused.enabled
    assert (await session.scalar(select(BotDelivery).where(BotDelivery.dedupe_key == 'disabled-client'))).status == 'cancelled'


async def test_invalid_sync_is_atomic_and_activity_is_isolated(client, admin_headers, session):
    a_id, a = await access(client, admin_headers, 'A')
    b_id, b = await access(client, admin_headers, 'B')
    for headers in [a, b]:
        assert (await client.post('/api/bot/configuration', headers=headers, json={'targets': [target()]})).status_code == 200
    bad = target('错误配置')
    bad['daily_time'] = '25:99'
    assert (await client.post('/api/bot/configuration', headers=a, json={'targets': [bad]})).status_code == 422
    assert (await client.post('/api/bot/configuration', headers=a, json={'targets': [target(), target()]})).status_code == 422
    assert (await client.get('/api/bot/targets', headers=a)).json()[0]['name'] == '测试会话'
    stamp = utcnow().isoformat()
    await client.post('/api/bot/activity', headers=a, json={'activities': {'qq:GroupMessage:shared': stamp, 'other:FriendMessage:user': stamp}})
    assert await get_setting(session, 'bot_activity_'+a_id) == {'qq:GroupMessage:shared': stamp}
    assert await get_setting(session, 'bot_activity_'+b_id) is None
