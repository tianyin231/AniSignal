from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.api.bot import TargetBody
from app.clock import utcnow
from app.models import BotDelivery, BotTarget, ContentVersion, RawContent, Source
from app.services import bot
from app.services.settings import set_setting


async def target(session, **overrides):
    body = TargetBody(name="我的私聊", umo="bot:FriendMessage:self", enabled=True, **overrides)
    row = BotTarget(name=body.name, umo=body.umo, enabled=body.enabled,
                    config=body.model_dump(exclude={"name", "umo", "enabled"}),
                    created_at=datetime(2026, 9, 10), last_scan_at=datetime(2026, 9, 10))
    session.add(row)
    await session.commit()
    return row


async def test_admin_crud_and_tool_auth(client, session, admin_headers):
    assert (await client.get('/api/admin/bot')).status_code in (401, 422)
    saved = await client.put('/api/admin/bot/bridge', headers=admin_headers,
                             json={"endpoint": "http://bot:6185/api/v1/im/messages", "api_key": "secret-never-echo"})
    assert saved.status_code == 200
    bad = await client.put('/api/admin/bot/bridge', headers=admin_headers,
                           json={"endpoint": "http://bot:6185/api?key=secret"})
    assert bad.status_code == 422
    body = TargetBody(name="测试", umo="bot:FriendMessage:self", enabled=True, tools=["digest"]).model_dump()
    result = await client.post('/api/admin/bot/targets', headers=admin_headers, json=body)
    assert result.status_code == 200
    tid = result.json()["id"]
    assert (await client.post('/api/admin/bot/targets', headers=admin_headers, json=body)).status_code == 409
    assert 'secret-never-echo' not in (await client.get('/api/admin/bot', headers=admin_headers)).text
    await set_setting(session, "bot_bridge", {"tools_key": "readonly"})
    assert (await client.get('/api/bot/targets')).status_code == 401
    headers = {"X-Bot-Key": "readonly"}
    assert (await client.post('/api/bot/query', headers=headers, json={"umo": body["umo"]})).status_code == 200
    assert (await client.post('/api/bot/query', headers=headers, json={"umo": body["umo"], "tool": "products"})).status_code == 403
    assert (await client.post('/api/bot/query', headers=headers, json={"umo": "bot:FriendMessage:other"})).status_code == 403
    body['enabled'] = False
    await client.put(f'/api/admin/bot/targets/{tid}', headers=admin_headers, json=body)
    assert (await client.post('/api/bot/query', headers=headers, json={"umo": body["umo"]})).status_code == 403


async def test_daily_dedupe_retry_and_disabled(session, monkeypatch):
    row = await target(session)
    calls = []
    async def send(*args):
        calls.append(args)
        return ("failed", "denied") if len(calls) == 1 else ("accepted", None)
    monkeypatch.setattr(bot, "send_message", send)
    now = datetime(2026, 9, 11, 0, 0)
    await bot.run_bot_notifications(session, now)
    delivery = await session.scalar(select(BotDelivery))
    assert delivery.kind == "daily" and delivery.status == "failed"
    delivery.updated_at = now
    await session.commit()
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    assert len(calls) == 1
    await bot.run_bot_notifications(session, now + timedelta(minutes=6))
    assert len(calls) == 2 and delivery.status == "accepted"
    await bot.run_bot_notifications(session, now + timedelta(minutes=7))
    assert len(calls) == 2
    row.enabled = False
    await session.commit()
    await bot.run_bot_notifications(session, now + timedelta(days=1))
    assert len(calls) == 2


async def test_new_target_does_not_backfill_today(session, monkeypatch):
    row = await target(session)
    row.created_at = datetime(2026, 9, 11, 1)
    await session.commit()
    await bot.run_bot_notifications(session, datetime(2026, 9, 11, 2))
    assert not list(await session.scalars(select(BotDelivery)))


async def test_unknown_is_not_automatically_retried(session, monkeypatch):
    row = await target(session)
    calls = []
    async def send(*args):
        calls.append(args)
        return 'unknown', 'timeout'
    monkeypatch.setattr(bot, 'send_message', send)
    delivery = await bot.create_delivery(session, row, 'manual', 'test', 'unique')
    await bot.deliver(session, row, delivery)
    await bot.deliver(session, row, delivery)
    assert len(calls) == 1 and delivery.status == 'unknown'


@pytest.mark.parametrize('body,status', [({'status':'ok'},'accepted'), ({'status':'error','message':'secret'},'failed')])
async def test_gateway_checks_response_body(monkeypatch, body, status):
    original = httpx.AsyncClient
    def handler(request):
        assert request.headers['X-API-Key'] == 'private'
        assert not request.url.query
        return httpx.Response(200, json=body)
    monkeypatch.setattr(bot.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler)))
    result, error = await bot.send_message({'endpoint':'http://bot/im/messages','api_key':'private'}, 'bot:FriendMessage:self', 'hello')
    assert result == status
    assert 'secret' not in (error or '')


async def test_scoped_queries_exclude_hidden_and_old_updates(session, monkeypatch):
    now = utcnow()
    row = await target(session, community='wuwa', keywords=['键盘'], daily_enabled=False, updates_enabled=True, quiet_start=0, quiet_end=0)
    row.last_scan_at = now - timedelta(minutes=5)
    source = Source(platform='bilibili', external_id='official', name='官方', identity='official')
    session.add(source)
    await session.flush()
    for n, (community, hidden, age) in enumerate([('wuwa',False,1),('hsr',False,1),('wuwa',True,1),('wuwa',False,10)]):
        raw = RawContent(platform='bilibili', content_type='video', external_id=str(n), source_id=source.id, community=community, hidden=hidden,
                         collected_at=now-timedelta(minutes=age), published_at=now-timedelta(hours=1))
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, title=f'键盘消息{n}', text='发布公告', category='announcement'))
    await session.commit()
    payload = await bot.query_target(session, row, now=now, collected_after=row.last_scan_at)
    assert len(payload['items']) == 1 and '消息0' in payload['text']
    calls = []
    async def send(*args):
        calls.append(args)
        return 'accepted', None
    monkeypatch.setattr(bot, 'send_message', send)
    await bot.run_bot_notifications(session, now)
    await bot.run_bot_notifications(session, now + timedelta(minutes=1))
    assert len(calls) == 1
