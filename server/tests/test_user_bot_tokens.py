"""用户 Token 从账号中心签发，接入隔离、轮换和撤销贯穿查询与投递。"""

import hashlib
from datetime import timedelta

import pytest

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotClient, BotDelivery, BotTarget, User
from app.services import bot, bot_clients
from app.services.auth import SESSION_COOKIE, create_session, hash_password
from app.services.settings import set_setting


async def account(session, email='owner@example.com'):
    user = User(email=email, display_name=email.split('@')[0], password_hash=hash_password('password123'), is_verified=True)
    session.add(user)
    await session.commit()
    cookie = await create_session(session, user)
    return user, {'Cookie': f'{SESSION_COOKIE}={cookie}'}


async def issue(client, headers, name='我的 AstrBot'):
    response = await client.post('/api/account/bot-clients', headers=headers, json={'name': name})
    assert response.status_code == 200
    data = response.json()
    return data, {'X-Bot-Key': data['token']}


def settings(umo='qq:GroupMessage:shared'):
    return TargetBody(name='用户群聊', umo=umo, enabled=True, delivery_mode='astrbot', daily_enabled=False,
        channels=['wuwa:official:youtube']).model_dump()


async def test_account_token_is_hashed_and_can_only_authenticate_bot_api(client, session, admin_headers):
    assert (await client.get('/api/account/bot-clients')).status_code == 401
    user, cookie = await account(session)
    data, key = await issue(client, cookie)
    row = await session.get(BotClient, data['id'])
    assert row.user_id == user.id and row.token_hash == hashlib.sha256(data['token'].encode()).hexdigest()
    assert row.token_tail == data['token'][-4:]
    identity = await client.get('/api/bot/identity', headers=key)
    assert identity.status_code == 200 and identity.json()['user']['id'] == user.id and not identity.json()['legacy']
    listing = await client.get('/api/account/bot-clients', headers=cookie)
    assert data['token'] not in listing.text and row.token_hash not in listing.text
    assert listing.json()[0]['token_tail'] == row.token_tail
    assert (await client.get('/api/account/overview', headers=key)).status_code == 401
    assert (await client.get('/api/admin/bot', headers={'X-Admin-Token': data['token']})).status_code == 401
    assert (await client.post(f"/api/admin/bot/clients/{row.id}/rotate", headers=admin_headers)).status_code == 409


async def test_other_account_cannot_list_rotate_or_revoke_my_token(client, session):
    _, a = await account(session, 'a@example.com')
    _, b = await account(session, 'b@example.com')
    data, key = await issue(client, a)
    assert (await client.get('/api/account/bot-clients', headers=b)).json() == []
    assert (await client.post(f"/api/account/bot-clients/{data['id']}/rotate", headers=b)).status_code == 404
    assert (await client.delete(f"/api/account/bot-clients/{data['id']}", headers=b)).status_code == 404
    assert (await client.get('/api/bot/identity', headers=key)).status_code == 200


async def test_user_tokens_keep_same_umo_and_outbox_isolated(client, session):
    users = [await account(session, email) for email in ('a@example.com', 'b@example.com')]
    keys = []
    target_ids = []
    for _, cookie in users:
        _, key = await issue(client, cookie)
        response = await client.post('/api/bot/configuration', headers=key, json={'targets': [settings()]})
        assert response.status_code == 200
        target_ids.append(response.json()['targets'][0]['id'])
        keys.append(key)
    assert target_ids[0] != target_ids[1]
    for index, target_id in enumerate(target_ids):
        session.add(BotDelivery(target_id=target_id, kind='manual', body=f'用户 {index} 的资料', dedupe_key=f'user-token-{index}'))
    await session.commit()
    delivery = (await client.post('/api/bot/outbox/claim', headers=keys[1], json={})).json()['delivery']
    assert delivery['body'] == '用户 1 的资料'
    path = f"/api/bot/outbox/{delivery['id']}/complete"
    receipt = {'receipt': delivery['receipt'], 'status': 'accepted'}
    assert (await client.post(path, headers=keys[0], json=receipt)).status_code == 409
    assert (await client.post(path, headers=keys[1], json=receipt)).status_code == 200


async def test_rotation_preserves_subscription_history_and_cursor(client, session):
    _, cookie = await account(session)
    data, old = await issue(client, cookie)
    response = await client.post('/api/bot/configuration', headers=old, json={'targets': [settings()]})
    target_id = response.json()['targets'][0]['id']
    row = await session.get(BotTarget, target_id)
    cursor = utcnow() - timedelta(hours=2)
    row.last_scan_at = cursor
    history = BotDelivery(target_id=row.id, kind='manual', body='历史', status='accepted', dedupe_key='rotation-history')
    session.add(history)
    await session.commit()
    changed = (await client.post(f"/api/account/bot-clients/{data['id']}/rotate", headers=cookie)).json()
    new = {'X-Bot-Key': changed['token']}
    assert changed['id'] == data['id']
    assert (await client.get('/api/bot/identity', headers=old)).status_code == 401
    assert (await client.get('/api/bot/targets', headers=new)).json()[0]['id'] == target_id
    await session.refresh(row)
    assert row.last_scan_at == cursor and (await session.get(BotDelivery, history.id)).status == 'accepted'


async def test_revocation_cancels_queue_and_stale_worker_cannot_send(client, session):
    _, cookie = await account(session)
    data, key = await issue(client, cookie)
    response = await client.post('/api/bot/configuration', headers=key, json={'targets': [settings()]})
    target = await session.get(BotTarget, response.json()['targets'][0]['id'])
    queued = await bot.create_delivery(session, target, 'manual', '待发送', 'revoke-user-token')
    assert (await client.delete(f"/api/account/bot-clients/{data['id']}", headers=cookie)).status_code == 200
    await session.refresh(queued)
    assert queued.status == 'cancelled'
    assert (await client.post('/api/bot/outbox/claim', headers=key, json={})).status_code == 401
    late = await bot.create_delivery(session, target, 'manual', '旧进程', 'revoked-stale-worker')
    assert not await bot.prepare_delivery(session, late, target=target)
    assert late.status == 'cancelled'


@pytest.mark.parametrize('change', [{'status': 'banned'}, {'is_verified': False}])
async def test_disabled_owner_loses_bot_access_and_pending_delivery(client, session, change):
    user, cookie = await account(session)
    _, key = await issue(client, cookie)
    response = await client.post('/api/bot/configuration', headers=key, json={'targets': [settings()]})
    target = await session.get(BotTarget, response.json()['targets'][0]['id'])
    queued = await bot.create_delivery(session, target, 'manual', '待发送', 'blocked-owner')
    for name, value in change.items():
        setattr(user, name, value)
    await session.commit()
    assert (await client.get('/api/bot/identity', headers=key)).status_code == 401
    assert not await bot.prepare_delivery(session, queued, target=target)
    assert queued.status == 'cancelled'


async def test_binding_legacy_client_retires_old_key_without_recreating_targets(client, session):
    user, cookie = await account(session)
    await set_setting(session, 'bot_bridge', {'tools_key': 'old-legacy-key'})
    old = {'X-Bot-Key': 'old-legacy-key'}
    target = (await client.post('/api/bot/configuration', headers=old, json={'targets': [settings()]})).json()['targets'][0]
    _, token = await bot_clients.create_user_client(session, user, '现有 AstrBot', client_id='legacy')
    key = {'X-Bot-Key': token}
    assert (await client.get('/api/bot/targets', headers=old)).status_code == 401
    assert (await client.get('/api/bot/targets', headers=key)).json()[0]['id'] == target['id']
    assert (await client.get('/api/bot/identity', headers=key)).json()['user']['id'] == user.id
    assert (await client.post('/api/bot/query', headers=key, json={'umo': target['umo'], 'tool': 'codes'})).status_code == 403
    listing = (await client.get('/api/account/bot-clients', headers=cookie)).json()
    assert listing[0]['target_count'] == 1
