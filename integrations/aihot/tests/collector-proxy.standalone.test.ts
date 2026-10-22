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
const { fetch: undiciFetch } = createRequire(new URL('../packages/backend/package.json', import.meta.url))('undici') as typeof import('undici');

async function listen(server: net.Server) {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  return (server.address() as net.AddressInfo).port;
}

test("AIHOT leases proxies at runtime, keeps redirects on one exit and sends proxy auth only to the proxy", async () => {
  const sockets = new Set<net.Socket>();
  const requests: string[] = [];
  const auth: string[] = [];
  const targets = ["first", "second"].map(name => http.createServer((req, res) => {
    requests.push(`${name}:${req.url}`);
    assert.equal(req.headers['proxy-authorization'], undefined);
    if (req.url === '/start') { res.writeHead(302, { location: '/feed' }); res.end(); }
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
    const port = ports[leases++ % ports.length];
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ url: `http://alice:s%40cret@127.0.0.1:${port}` }));
  });
  const saved = config.collectorProxyUrl;
  const savedToken = process.env.ANISIGNAL_BRIDGE_TOKEN;
  config.collectorProxyUrl = `http://127.0.0.1:${await listen(broker)}/lease`;
  process.env.ANISIGNAL_BRIDGE_TOKEN = 'test-bridge-token';
  try {
    assert.equal((await guardedFetch('http://93.184.216.34/start')).text(), 'first');
    assert.equal((await guardedFetch('http://93.184.216.34/feed', { route: 'direct' })).text(), 'second');
    assert.equal(leases, 2, 'a redirect does not lease another exit');
    assert.deepEqual(requests, ['first:/start', 'first:/feed', 'second:/feed']);
    assert.ok(auth.length >= 2 && auth.every(value => value === `Basic ${Buffer.from('alice:s@cret').toString('base64')}`));
    await assert.rejects(guardedFetch('http://169.254.169.254/latest/meta-data/'), /Blocked/);
    assert.equal(requests.length, 3, 'metadata is never requested through the proxy');
  } finally {
    config.collectorProxyUrl = saved;
    if (savedToken === undefined) delete process.env.ANISIGNAL_BRIDGE_TOKEN;
    else process.env.ANISIGNAL_BRIDGE_TOKEN = savedToken;
    for (const socket of sockets) socket.destroy();
    await Promise.all([broker, ...proxies].map(server => new Promise<void>(resolve => server.close(() => resolve()))));
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
