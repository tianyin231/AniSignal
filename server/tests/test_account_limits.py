"""公测额度从账号、插件同步一直约束到真实发送入口。"""

from datetime import datetime
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.models import BotDelivery, CustomFeed, NotificationDelivery
from app.services.account_limits import claim_notification, quota_view
from app.services.notifications import send_admin_email
from app.services.settings import set_setting
from tests.test_user_bot_tokens import account, issue, settings


async def test_free_signup_has_three_monitors_at_hourly_intervals(client, session):
    user, cookie = await account(session)
    quota = (await client.get('/api/account/custom-quota', headers=cookie)).json()
    assert quota['admitted'] and quota['max_feeds'] == 3 and quota['min_interval_minutes'] == 60
    body = {'name': '测试', 'kind': 'keyword', 'target_value': '测试目标'}
    assert (await client.post('/api/account/custom-feeds', headers=cookie,
                             json={**body, 'interval_minutes': 30})).status_code == 400
    for i in range(3):
        response = await client.post('/api/account/custom-feeds', headers=cookie,
                                     json={**body, 'target_value': f'目标{i}'})
        assert response.status_code == 201
        assert (await session.get(CustomFeed, response.json()['id'])).interval_minutes == 60
    assert (await client.post('/api/account/custom-feeds', headers=cookie, json=body)).status_code == 400


async def test_one_token_and_three_enabled_sessions_are_enforced_server_side(client, session):
    _, cookie = await account(session)
    data, key = await issue(client, cookie)
    assert (await client.post('/api/account/bot-clients', headers=cookie, json={'name': '第二个'})).status_code == 409
    targets = [settings(f'qq:GroupMessage:{i}') for i in range(4)]
    assert (await client.post('/api/bot/configuration', headers=key, json={'targets': targets[:3]})).status_code == 200
    assert (await client.post('/api/bot/configuration', headers=key, json={'targets': targets})).status_code == 409
    assert len((await client.get('/api/bot/targets', headers=key)).json()) == 3
    quota = (await client.get('/api/account/bot-clients/quota', headers=cookie)).json()
    assert quota['bot_clients_used'] == 1 and quota['bot_targets_used'] == 3
    assert quota['daily_notifications'] == 20
    assert (await client.delete(f"/api/account/bot-clients/{data['id']}", headers=cookie)).status_code == 200
    await issue(client, cookie, '替换接入')


async def test_admin_can_increase_all_limits_and_tokens_share_session_allowance(client, session, admin_headers):
    user, cookie = await account(session)
    override = {'max_feeds': 10, 'min_interval_minutes': 30, 'max_bot_clients': 2,
                'max_bot_targets': 3, 'daily_notifications': 100}
    response = await client.put('/api/admin/custom-feeds/policy', headers=admin_headers,
                               json={'user_overrides': {str(user.id): override}})
    assert response.status_code == 200
    keys = [(await issue(client, cookie, f'接入{i}'))[1] for i in range(2)]
    for key in keys:
        response = await client.post('/api/bot/configuration', headers=key,
                                    json={'targets': [settings('qq:GroupMessage:one')]})
        assert response.status_code == 200
    # 两个接入不能各自拿到三会话额度；拒绝时原来的会话保持启用。
    two = [settings(f'qq:GroupMessage:{i}') for i in range(2)]
    assert (await client.post('/api/bot/configuration', headers=keys[0], json={'targets': two})).status_code == 200
    assert (await client.post('/api/bot/configuration', headers=keys[1], json={'targets': two})).status_code == 409
    assert (await client.get('/api/account/custom-quota', headers=cookie)).json()['max_feeds'] == 10
    assert (await client.get('/api/account/bot-clients/quota', headers=cookie)).json()['daily_notifications'] == 100


async def test_mail_and_bot_share_daily_budget_and_failures_count(client, session, monkeypatch):
    user, cookie = await account(session)
    user.notification_enabled = True
    await set_setting(session, 'custom_feed_policy', {'daily_notifications_per_user': 2})
    _, key = await issue(client, cookie)
    config = {**settings(), 'reminders_enabled': True, 'hourly_limit': 0, 'min_push_interval_minutes': 0}
    response = await client.post('/api/bot/configuration', headers=key, json={'targets': [config]})
    target_id = response.json()['targets'][0]['id']
    mail = NotificationDelivery(user_id=user.id, kind='custom', source='schedule', period_date=datetime.now().date(),
                                status='pending', subject='定制摘要', body='测试')
    session.add(mail)
    session.add_all([BotDelivery(target_id=target_id, kind='reminder', body='提醒', dedupe_key=f'quota:{i}') for i in range(2)])
    await session.commit()
    send = AsyncMock(return_value=False)
    monkeypatch.setattr('app.services.notifications.send_email', send)
    assert (await send_admin_email(session, mail.id))['status'] == 'failed'
    assert (await send_admin_email(session, mail.id))['status'] == 'skipped'
    assert send.await_count == 1
    assert (await client.post('/api/bot/outbox/claim', headers=key, json={})).json()['delivery'] is not None
    assert (await client.post('/api/bot/outbox/claim', headers=key, json={})).json()['delivery'] is None
    rows = list(await session.scalars(select(BotDelivery).order_by(BotDelivery.id)))
    await session.refresh(rows[-1])
    assert rows[-1].status == 'cancelled' and '额度' in rows[-1].error
    assert (await quota_view(session, user.id))['notifications_used'] == 2


async def test_budget_resets_at_beijing_midnight_and_other_users_are_isolated(session):
    a, _ = await account(session, 'a@example.com')
    b, _ = await account(session, 'b@example.com')
    await set_setting(session, 'custom_feed_policy', {'daily_notifications_per_user': 1})
    before = datetime(2026, 11, 2, 15, 59)
    midnight = datetime(2026, 11, 2, 16, 0)
    assert await claim_notification(session, a.id, now=before)
    await session.commit()
    assert not await claim_notification(session, a.id, now=before)
    await session.commit()
    assert await claim_notification(session, b.id, now=before)
    await session.commit()
    assert (await quota_view(session, a.id, now=midnight))['notifications_used'] == 0
    assert await claim_notification(session, a.id, now=midnight)
    await session.commit()


async def test_admin_mail_does_not_consume_customer_notification_budget(session, monkeypatch):
    user, _ = await account(session)
    await set_setting(session, 'custom_feed_policy', {'daily_notifications_per_user': 1})
    assert await claim_notification(session, user.id)
    await session.commit()
    mail = NotificationDelivery(user_id=user.id, kind='admin', source='manual', period_date=datetime.now().date(),
                                status='pending', subject='管理员通知', body='测试')
    session.add(mail)
    await session.commit()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr('app.services.notifications.send_email', send)
    assert (await send_admin_email(session, mail.id))['status'] == 'sent'
    assert (await quota_view(session, user.id))['notifications_used'] == 1
