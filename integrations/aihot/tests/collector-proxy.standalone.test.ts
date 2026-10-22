import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { createRequire } from "node:module";
import { test, mock } from "node:test";
import { SocksClient } from "socks";
import { config } from "../packages/backend/src/config.ts";
import { guardedFetch } from "../packages/backend/src/lib/http-fetch.ts";
import { createEgressProxy } from "../packages/backend/src/lib/egress-proxy.ts";
// 引擎锁定 undici 8；测试根目录的工具依赖可能另带 undici 7。
const { fetch: undiciFetch, MockAgent, getGlobalDispatcher, setGlobalDispatcher } = createRequire(new URL('../packages/backend/package.json', import.meta.url))('undici') as typeof import('undici');

async function listen(server: net.Server) {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  return (server.address() as net.AddressInfo).port;
}

test("AIHOT uses direct first, falls back in priority order and keeps redirects and authentication on one exit", async () => {
  const sockets = new Set<net.Socket>();
  const requests: string[] = [];
  const auth: string[] = [];
  const targets = ["first", "second"].map(name => http.createServer((req, res) => {
    requests.push(`${name}:${req.url}`);
    assert.equal(req.headers['proxy-authorization'], undefined);
    if (name === 'first') { res.writeHead(503); res.end('temporary failure'); }
    else if (req.url === '/start') { res.writeHead(302, { location: '/feed' }); res.end(); }
    else res.end(name);
  }));
  const proxies = targets.map(target => {
    const proxy = http.createServer();
    proxy.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); });
    proxy.on('connect', (req, socket, head) => {
      auth.push(String(req.headers['proxy-authorization']));
      assert.equal(req.url, '93.184.216.34:80');
      socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      target.emit('connection', socket);
      if (head.length) socket.unshift(head);
    });
    return proxy;
  });
  const ports = await Promise.all(proxies.map(listen));
  let leases = 0;
  const broker = http.createServer((req, res) => {
    assert.equal(req.method, 'POST');
    assert.equal(req.headers['x-anisignal-key'], 'test-bridge-token');
    leases++;
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ routes: [null, ...ports.map(port => `http://alice:s%40cret@127.0.0.1:${port}`)] }));
  });
  const saved = config.collectorProxyUrl;
  const savedToken = process.env.ANISIGNAL_BRIDGE_TOKEN;
  const savedPrivate = config.allowPrivateNetworkFetch;
  const savedDispatcher = getGlobalDispatcher();
  const direct = new MockAgent();
  direct.disableNetConnect();
  direct.enableNetConnect(/^127\.0\.0\.1:/);
  const origin = direct.get('http://93.184.216.34');
  origin.intercept({ path: '/direct' }).reply(200, 'direct');
  origin.intercept({ path: '/empty' }).reply(200, '');
  origin.intercept({ path: '/start' }).reply(503, 'direct failed');
  origin.intercept({ path: '/slow' }).reply(200, 'too late').delay(150);
  origin.intercept({ path: '/post', method: 'POST' }).reply(503, 'direct post failed');
  setGlobalDispatcher(direct);
  config.allowPrivateNetworkFetch = true;
  config.collectorProxyUrl = `http://127.0.0.1:${await listen(broker)}/lease`;
  process.env.ANISIGNAL_BRIDGE_TOKEN = 'test-bridge-token';
  try {
    assert.equal((await guardedFetch('http://93.184.216.34/direct')).text(), 'direct');
    assert.equal((await guardedFetch('http://93.184.216.34/empty')).text(), '');
    assert.deepEqual(requests, [], 'successful direct reads, including empty bodies, never use a proxy');
    assert.equal((await guardedFetch('http://93.184.216.34/start')).text(), 'second');
    assert.equal(leases, 3, 'a redirect or fallback does not lease another plan');
    assert.deepEqual(requests, ['first:/start', 'second:/start', 'second:/feed']);
    assert.equal((await guardedFetch('http://93.184.216.34/slow', { timeoutMs: 80 })).text(), 'second');
    const beforePost = requests.length;
    assert.equal((await guardedFetch('http://93.184.216.34/post', { method: 'POST', body: 'paid' })).status, 503);
    assert.equal(requests.length, beforePost, 'a POST is never replayed through another exit');
    assert.ok(auth.length >= 2 && auth.every(value => value === `Basic ${Buffer.from('alice:s@cret').toString('base64')}`));
    config.allowPrivateNetworkFetch = false;
    await assert.rejects(guardedFetch('http://169.254.169.254/latest/meta-data/'), /Blocked/);
    assert.equal(requests.length, beforePost, 'metadata is never requested through the proxy');
  } finally {
    setGlobalDispatcher(savedDispatcher);
    config.allowPrivateNetworkFetch = savedPrivate;
    config.collectorProxyUrl = saved;
    if (savedToken === undefined) delete process.env.ANISIGNAL_BRIDGE_TOKEN;
    else process.env.ANISIGNAL_BRIDGE_TOKEN = savedToken;
    for (const socket of sockets) socket.destroy();
    await Promise.all([broker, ...proxies].map(server => new Promise<void>(resolve => server.close(() => resolve()))));
    await direct.close();
  }
});

test('SOCKS5 connects to the checked IP and decodes authentication without leaking options', async () => {
  const target = http.createServer((_req, res) => res.end('socks-route'));
  const port = await listen(target);
  const calls: unknown[] = [];
  const mocked = mock.method(SocksClient, 'createConnection', async options => {
    calls.push(options);
    const socket = net.connect(port, '127.0.0.1');
    await new Promise<void>((resolve, reject) => { socket.once('connect', resolve); socket.once('error', reject); });
    return { socket };
  });
  const agent = createEgressProxy('socks5://alice:s%40cret@proxy.invalid:1080', async () => ['93.184.216.34']);
  try {
    assert.equal(await (await undiciFetch('http://public.invalid/feed', { dispatcher: agent })).text(), 'socks-route');
    const first = calls[0] as { destination: { host: string; port: number }; proxy: { userId: string; password: string } };
    assert.deepEqual(first.destination, { host: '93.184.216.34', port: 80 });
    assert.equal(first.proxy.userId, 'alice');
    assert.equal(first.proxy.password, 's@cret');
  } finally {
    mocked.mock.restore();
    await agent.close();
    await new Promise<void>(resolve => target.close(() => resolve()));
  }
  const blocked = createEgressProxy('socks5://alice:secret@proxy.invalid:1080', async () => ['169.254.169.254']);
  try {
    await assert.rejects(undiciFetch('http://private.invalid/feed', { dispatcher: blocked }));
    assert.equal(calls.length, 1, 'a blocked destination never opens a SOCKS connection');
  } finally { await blocked.close(); }
});
