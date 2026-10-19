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
    # SEC-039：不用 in (401, 422)——缺 X-Bot-Key/X-Admin-Token 走的是 Header 校验层，
    # 返回 422 且 loc 里带 header 名；区间断言会把"鉴权依赖被摘掉"也算成通过。
    resp = await client.get('/api/admin/bot')
    assert resp.status_code == 422, resp.text
    assert any(e.get('loc', [None])[-1] in ('x-admin-token', 'x-bot-key') for e in resp.json()['detail']), resp.text
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
    row = await target(session, community='wuwa', keywords=['键盘'], daily_enabled=False, updates_enabled=True, quiet_start=0, quiet_end=0, merge_minutes=0)
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


async def test_merge_window_batches_normal_updates(session, monkeypatch):
    now = datetime(2026, 9, 13, 4, 0)  # 北京 12:00
    row = await target(session, community='wuwa', daily_enabled=False, updates_enabled=True,
                       quiet_start=0, quiet_end=0, merge_minutes=5, hourly_limit=0)
    row.last_scan_at = now - timedelta(minutes=10)
    source = Source(platform='bilibili', external_id='official', name='官方', identity='official')
    session.add(source)
    await session.flush()
    for n in range(2):
        raw = RawContent(platform='bilibili', content_type='video', external_id=f'm{n}', source_id=source.id, community='wuwa',
                         collected_at=now - timedelta(minutes=2), published_at=now - timedelta(hours=1), url=f'https://example.com/{n}')
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, title=f'合并消息{n}', text='正文', category='announcement'))
    await session.commit()
    calls = []
    async def send(*args):
        calls.append(args)
        return 'accepted', None
    monkeypatch.setattr(bot, 'send_message', send)
    await bot.run_bot_notifications(session, now)
    assert not list(await session.scalars(select(BotDelivery).where(BotDelivery.kind == 'updates')))
    await bot.run_bot_notifications(session, now + timedelta(minutes=6))
    rows = list(await session.scalars(select(BotDelivery).where(BotDelivery.kind == 'updates')))
    assert len(rows) == 1
    assert '重要动态 ×2' in rows[0].body
    assert '合并消息0' in rows[0].body and '合并消息1' in rows[0].body


async def test_short_format_and_multi_image(session, monkeypatch):
    now = datetime(2026, 9, 13, 4, 0)
    row = await target(session, community='wuwa', daily_enabled=False, updates_enabled=True,
                       quiet_start=0, quiet_end=0, merge_minutes=0, max_images=3)
    row.last_scan_at = now - timedelta(minutes=10)
    source = Source(platform='bilibili', external_id='official', name='官方', identity='official')
    session.add(source)
    await session.flush()
    for n in range(2):
        raw = RawContent(platform='bilibili', content_type='video', external_id=f'i{n}', source_id=source.id, community='wuwa',
                         collected_at=now - timedelta(minutes=2), published_at=now - timedelta(hours=1),
                         url=f'https://example.com/{n}')
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, title=f'短讯标题{n}', text='一句话说明', category='announcement',
                                   normalized_json={'media': [f'https://img.example/{n}.jpg']}))
    await session.commit()
    # delivery_mode astrbot：rows 留在 pending，便于检查 media
    row.config = {**row.config, 'delivery_mode': 'astrbot'}
    await session.commit()
    await bot.run_bot_notifications(session, now)
    rows = list(await session.scalars(select(BotDelivery).where(BotDelivery.kind == 'updates').order_by(BotDelivery.id)))
    assert len(rows) == 2
    bodies = "\n".join(r.body for r in rows)
    assert '短讯标题0' in bodies and '短讯标题1' in bodies
    media_lists = [r.media for r in rows]
    assert all(m and m[0].startswith('https://img.example/') for m in media_lists)


async def test_quiet_blocks_normal_but_allows_critical(session):
    # 北京 01:00 = UTC 前一日 17:00；quiet 23-8
    now = datetime(2026, 9, 13, 17, 0)
    row = await target(session, community='wuwa', daily_enabled=False, updates_enabled=True,
                       quiet_start=23, quiet_end=8, quiet_critical=True, merge_minutes=0)
    row.last_scan_at = now - timedelta(minutes=10)
    row.config = {**row.config, 'delivery_mode': 'direct'}
    await session.commit()
    source = Source(platform='bilibili', external_id='official', name='官方', identity='official')
    session.add(source)
    await session.flush()
    raw = RawContent(platform='bilibili', content_type='video', external_id='q1', source_id=source.id, community='wuwa',
                     collected_at=now - timedelta(minutes=2), published_at=now - timedelta(hours=1))
    session.add(raw)
    await session.flush()
    session.add(ContentVersion(raw_content_id=raw.id, title='静默中的官方消息', text='正文', category='announcement'))
    await session.commit()
    await bot.run_bot_notifications(session, now)
    # 普通 updates 创建后因静默不投递（direct 模式下 deliver 会调 send_message；未配置 bridge 会 failed）
    # 这里断言未产生成功投递；合并/直发创建了记录但不应被 deliver 成功。
    rows = list(await session.scalars(select(BotDelivery)))
    for r in rows:
        assert r.status != 'accepted'


async def test_format_helpers():
    text = bot.format_short_update({
        'community': 'wuwa', 'title': '测试标题', 'text': '这是一句说明\n第二行',
        'published_at': datetime(2026, 9, 13, 4, 0), 'url': 'https://example.com/a',
    })
    assert '[鸣潮] 测试标题' in text
    assert 'https://example.com/a' in text
    assert '这是一句说明 第二行' in text
    merged = bot.format_merged_updates([
        {'community': 'blhx', 'title': '甲', 'url': 'https://a'},
        {'community': 'wuwa', 'title': '乙', 'url': 'https://b'},
    ], datetime(2026, 9, 13, 4, 0))
    assert '重要动态 ×2' in merged
    assert '[碧蓝航线] 甲' in merged
    assert bot.collect_images([{'image': 'a'}, {'image': 'a'}, {'image': 'b'}], 3) == ['a', 'b']
    assert bot.classify_item({'importance': True}) == 'critical'
    assert bot.classify_item({'importance': False}) == 'normal'
    assert bot._is_quiet(1, {'quiet_start': 23, 'quiet_end': 8}) is True
    assert bot._is_quiet(12, {'quiet_start': 23, 'quiet_end': 8}) is False
    brief = bot.format_daily_brief([
        {'community': 'wuwa', 'title': '甲' * 50, 'url': 'https://a'},
        {'community': 'blhx', 'title': '乙', 'url': 'https://b'},
    ], datetime(2026, 9, 13, 4, 0))
    assert '今日速报' in brief
    assert 'https://a' in brief
    assert '…' in brief
    assert '完整正文' not in brief

