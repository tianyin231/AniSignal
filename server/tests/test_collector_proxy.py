import asyncio
import base64

import httpx
import pytest

from app.services import collector_proxy as pool
from app.services.settings import set_setting
from app.models import SiteSetting


@pytest.mark.parametrize("text,scheme,host,port,user,password", [
    ("proxy.example:8080:alice:p@ss:w#rd", "http", "proxy.example", 8080, "alice", "p@ss:w#rd"),
    ("http://alice:p%40ss%3Aw%23rd@proxy.example:8080", "http", "proxy.example", 8080, "alice", "p@ss:w#rd"),
    ("alice:secret@proxy.example:8080", "http", "proxy.example", 8080, "alice", "secret"),
    ("proxy.example:8080", "http", "proxy.example", 8080, "", ""),
    ("https://proxy.example:8443", "https", "proxy.example", 8443, "", ""),
    ("socks5://alice:secret@proxy.example:1080", "socks5", "proxy.example", 1080, "alice", "secret"),
    ("socks5h://proxy.example:1080", "socks5", "proxy.example", 1080, "", ""),
    ("[2001:db8::1]:8080:alice:secret", "http", "2001:db8::1", 8080, "alice", "secret"),
    ("proxy.example,8080,alice,secret", "http", "proxy.example", 8080, "alice", "secret"),
    ("proxy.example\t8080\talice\tsecret", "http", "proxy.example", 8080, "alice", "secret"),
])
def test_proxy_formats(text, scheme, host, port, user, password):
    entry = pool.parse_proxy(text)
    assert (entry["scheme"], entry["host"], entry["port"], entry["username"], entry["password"]) == (scheme, host, port, user, password)
    assert pool.parse_proxy(pool.proxy_url(entry))["password"] == password


@pytest.mark.parametrize("text", ["ftp://proxy.example:80", "http://", "proxy.example:0", "proxy.example:65536", "http://proxy.example:80/path", "http://proxy.example:80?x=1", "http://proxy.example:80/#secret", "host\nname:80", "http://user:pa%0ass@proxy.example:80"])
def test_invalid_proxy_does_not_echo_credentials(text):
    with pytest.raises(ValueError) as exc:
        pool.parse_proxy(text)
    assert text not in str(exc.value)


async def test_proxy_admin_import_edit_delete_and_password_retention(client, admin_headers, session):
    # 有效行可以保存，重复行跳过；错误只回传行号和原因。
    result = await client.post('/api/admin/proxies', headers=admin_headers, json={
        'text': 'proxy.example:8080:alice:secret-one\nproxy.example:8080:alice:secret-one\nftp://bad:80\nsecond.example:8081:bob:secret-two',
    })
    assert result.status_code == 200
    body = result.json()
    assert (body['added'], body['duplicates'], body['errors'][0]['line']) == (2, 1, 3)
    assert 'secret-one' not in result.text and 'secret-two' not in result.text
    entry_id = body['pool']['entries'][0]['id']
    assert (await client.get('/api/admin/proxies', headers=admin_headers)).json()['entries'][0]['password_set']
    # 两次导入后再开一个请求，确保嵌套 JSON 列表确实落库。
    await client.post('/api/admin/proxies', headers=admin_headers, json={'text': 'third.example:80'})
    assert len((await client.get('/api/admin/proxies', headers=admin_headers)).json()['entries']) == 3
    await client.patch(f'/api/admin/proxies/{entry_id}', headers=admin_headers, json={'label': '出口 A', 'host': 'changed.example'})
    await session.refresh(await session.get(SiteSetting, pool.SETTING_KEY))
    stored = await pool.get_config(session)
    assert stored['entries'][0]['password'] == 'secret-one'
    assert stored['entries'][0]['host'] == 'changed.example'
    await client.patch(f'/api/admin/proxies/{entry_id}', headers=admin_headers, json={'password': ''})
    assert not (await client.get('/api/admin/proxies', headers=admin_headers)).json()['entries'][0]['password_set']
    await client.delete(f'/api/admin/proxies/{entry_id}', headers=admin_headers)
    assert len((await client.get('/api/admin/proxies', headers=admin_headers)).json()['entries']) == 2


async def test_route_priority_and_platform_opt_out(session):
    entries = [pool.parse_proxy(f'proxy-{n}.example:8080') for n in range(3)]
    entries[1]['enabled'] = False
    await set_setting(session, pool.SETTING_KEY, {'enabled': True, 'entries': entries, 'platforms': {'x': False}})
    assert await pool.collection_routes(session, 'bilibili') == [None, pool.proxy_url(entries[0]), pool.proxy_url(entries[2])]
    assert await pool.collection_routes(session, 'x') == [None]
    config = await pool.get_config(session)
    config['order'] = [entries[2]['id'], 'direct', entries[1]['id'], entries[0]['id']]
    await set_setting(session, pool.SETTING_KEY, config)
    assert await pool.collection_routes(session, 'aihot') == [pool.proxy_url(entries[2]), None, pool.proxy_url(entries[0])]


async def test_priority_api_persists_and_rejects_missing_routes(client, admin_headers):
    response = await client.post('/api/admin/proxies', headers=admin_headers, json={'text': 'one.example:80\ntwo.example:80'})
    ids = [entry['id'] for entry in response.json()['pool']['entries']]
    assert response.json()['pool']['order'] == ['direct', *ids]
    order = [ids[1], 'direct', ids[0]]
    response = await client.put('/api/admin/proxies', headers=admin_headers, json={'enabled': True, 'order': order})
    assert response.json()['order'] == order
    assert (await client.get('/api/admin/proxies', headers=admin_headers)).json()['order'] == order
    assert (await client.put('/api/admin/proxies', headers=admin_headers, json={'enabled': True, 'order': ids})).status_code == 422
    await client.delete(f'/api/admin/proxies/{ids[1]}', headers=admin_headers)
    assert (await client.get('/api/admin/proxies', headers=admin_headers)).json()['order'] == ['direct', ids[0]]


@pytest.mark.parametrize('direct_ok', [True, False])
async def test_fallback_only_after_failed_read_and_empty_is_success(session, direct_ok):
    entries = [pool.parse_proxy(f'proxy-{n}.example:80') for n in range(2)]
    await set_setting(session, pool.SETTING_KEY, {'enabled': True, 'entries': entries})
    collector = type('Collector', (), {})()
    calls = []
    async def fetch():
        calls.append(collector.proxy_url)
        if not direct_ok and collector.proxy_url != pool.proxy_url(entries[1]):
            raise httpx.ConnectError('connection failed')
        return []
    assert await pool.fetch_with_fallback(collector, session, 'bilibili', fetch) == []
    assert calls == ([None] if direct_ok else [None, *(pool.proxy_url(e) for e in entries)])


async def test_bili_direct_cooldown_keeps_proxy_available(session, monkeypatch):
    from app.collectors import bilibili
    entry = pool.parse_proxy('proxy.example:80')
    await set_setting(session, pool.SETTING_KEY, {'enabled': True, 'entries': [entry]})
    collector = bilibili.BilibiliCollector()
    calls = []
    async def run(*args):
        calls.append(collector.proxy_url)
        if not collector.proxy_url:
            raise bilibili.BiliRiskControlError('refused')
        return []
    monkeypatch.setattr(collector, '_run_once', run)
    monkeypatch.setattr(bilibili, '_CIRCUIT_STATE', {})
    for _ in range(3):
        assert await pool.fetch_with_fallback(collector, session, 'bilibili', lambda: collector._run('dynamics', '123')) == []
    assert calls == [None, pool.proxy_url(entry), None, pool.proxy_url(entry), pool.proxy_url(entry)]


async def test_fallback_final_error_does_not_leak_password(session):
    from app.collectors.base import CollectorError
    entry = pool.parse_proxy('proxy.example:80:alice:private-password')
    await set_setting(session, pool.SETTING_KEY, {'enabled': True, 'entries': [entry]})
    collector = type('Collector', (), {})()
    async def fetch():
        raise CollectorError(f'failed {collector.proxy_url}')
    with pytest.raises(CollectorError) as caught:
        await pool.fetch_with_fallback(collector, session, 'bilibili', fetch)
    assert 'private-password' not in str(caught.value)


async def test_admin_auth_and_internal_lease(client, admin_headers, monkeypatch):
    from app.config import get_settings
    assert (await client.get('/api/admin/proxies')).status_code in (401, 422)
    assert (await client.post('/api/internal/collector-proxy/lease')).status_code == 401
    assert (await client.put('/api/admin/proxies', headers=admin_headers, json={'enabled': True})).status_code == 400
    await client.post('/api/admin/proxies', headers=admin_headers, json={'text': 'proxy.example:8080:alice:secret'})
    await client.put('/api/admin/proxies', headers=admin_headers, json={'enabled': True, 'platforms': {'aihot': True}})
    monkeypatch.setattr(get_settings(), 'aihot_token', 'a' * 40)
    response = await client.post('/api/internal/collector-proxy/lease', headers={'X-Anisignal-Key': 'a' * 40})
    assert response.status_code == 200 and response.json()['routes'] == [None, 'http://alice:secret@proxy.example:8080']
    assert response.headers['cache-control'] == 'no-store'


async def test_connection_test_does_not_leak_password(client, admin_headers, monkeypatch):
    response = await client.post('/api/admin/proxies', headers=admin_headers, json={'text': 'proxy.example:80:alice:secret'})
    entry_id = response.json()['pool']['entries'][0]['id']
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['proxy'] == 'http://alice:secret@proxy.example:80' and kwargs['trust_env'] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): raise httpx.ProxyError('407 Proxy Authentication Required secret')
    monkeypatch.setattr('app.api.admin_proxies.httpx.AsyncClient', FakeClient)
    response = await client.post(f'/api/admin/proxies/{entry_id}/test', headers=admin_headers)
    assert response.json()['message'] == '代理认证失败（407）'
    assert 'secret' not in response.text


async def test_http_client_reaches_authenticated_proxy_instead_of_destination():
    hits = []
    async def proxy(reader, writer):
        headers = (await reader.readuntil(b'\r\n\r\n')).decode()
        hits.append(headers)
        body = b'{"via":"proxy"}'
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(proxy, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    collector = type('Collector', (), {})()
    pool.configure_collector(collector, f'http://alice:secret@127.0.0.1:{port}')
    try:
        async with httpx.AsyncClient(**pool.http_proxy_kwargs(collector)) as client:
            assert (await client.get('http://unreachable.invalid/feed')).json() == {'via': 'proxy'}
    finally:
        server.close()
        await server.wait_closed()
    assert hits[0].startswith('GET http://unreachable.invalid/feed HTTP/1.1')
    assert f'Proxy-Authorization: Basic {base64.b64encode(b"alice:secret").decode()}'.lower() in hits[0].lower()


async def test_bili_proxy_only_in_child_environment(monkeypatch):
    from app.collectors import bilibili
    class Process:
        returncode = 0
        async def communicate(self): return b'[]', b''
    async def execute(*args, **kwargs):
        assert kwargs['env']['BILI_PROXY'] == 'http://alice:secret@proxy.example:80'
        assert 'secret' not in str(args)
        assert 'HTTP_PROXY' not in kwargs['env']
        return Process()
    monkeypatch.setenv('HTTP_PROXY', 'http://inherited.invalid:80')
    monkeypatch.setattr(bilibili.asyncio, 'create_subprocess_exec', execute)
    collector = bilibili.BilibiliCollector()
    pool.configure_collector(collector, 'http://alice:secret@proxy.example:80')
    assert await collector._run_once('dynamics', '1955897084') == []
