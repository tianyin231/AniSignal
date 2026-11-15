import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import test from 'node:test';
import { AniSignalService } from '../lib/index.js';
import { configurationRevision, normalizeStoredConfig, normalizeViewSubmission } from '../lib/config.js';
import { PluginUpdateChecker, UPDATE_INTERVAL_MS } from '../lib/updates.js';

const SHA = 'a'.repeat(40);
const manifest = (version) => ({ name: 'dsh-plugin-anisignal', version, dsh: { bundle: { patch: './cordis.patch.yml' } } });
const json = (body) => new Response(JSON.stringify(body), { status: 200 });

test('updates: concurrent checks share requests, cache expires, and install pins the checked commit', async () => {
  let now = Date.UTC(2026, 10, 15);
  const calls = [];
  const checker = new PluginUpdateChecker({
    currentVersion: '1.14.8', now: () => now,
    fetchImpl: async (url, init) => {
      calls.push(url);
      assert.deepEqual(init.headers, { Accept: 'application/json' });
      return json(url.endsWith('/commits/main') ? { sha: SHA } : manifest('1.14.9'));
    },
  });
  const [first, second] = await Promise.all([checker.check(), checker.check()]);
  assert.deepEqual(first, second);
  assert.equal(calls.length, 2);
  assert.equal(first.available, true);
  assert.equal(first.spec, `github:tianyin231/DSH_Plugin_AniSignal#${SHA}`);
  assert.ok(calls[1].includes(`/${SHA}/package.json`));
  await checker.check();
  assert.equal(calls.length, 2);
  now += UPDATE_INTERVAL_MS;
  await checker.check();
  assert.equal(calls.length, 4);
  await checker.check({ force: true });
  assert.equal(calls.length, 6);
});

test('updates: failed checks remain retryable, invalid releases and downgrades cannot be installed', async () => {
  let response = 'offline';
  const checker = new PluginUpdateChecker({
    currentVersion: '1.14.9',
    fetchImpl: async (url) => {
      if (response === 'offline') throw new Error('network unavailable');
      return json(url.endsWith('/commits/main') ? { sha: SHA } : response);
    },
  });
  assert.match((await checker.check()).error, /network unavailable/);
  assert.equal(checker.snapshot().checking, false);
  response = { ...manifest('1.15.0'), name: 'unexpected-package' };
  assert.match((await checker.check({ force: true })).error, /版本信息无效/);
  assert.equal(checker.snapshot().available, false);
  for (const [version, available] of [['1.14.8', false], ['1.14.9', false], ['1.14.10', true], ['1.15.0', true], ['2.0.0', true]]) {
    response = manifest(version);
    const state = await checker.check({ force: true });
    assert.equal(state.error, '');
    assert.equal(state.available, available, version);
  }
});

test('updates route: preference persists without syncing or exposing secrets, manual check works when disabled', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'anisignal-updates-'));
  try {
    const routes = new Map();
    const checks = [];
    const service = new AniSignalService(null, { tools_key: 'private-token' }, {
      configPath: join(dir, 'config.json'),
      updateChecker: { check: async (opts) => checks.push(opts), snapshot: () => ({ current_version: '1.14.9', available: false }) },
      fetchImpl: async () => { throw new Error('must not sync AniSignal while saving update preference'); },
    });
    const dispose = service.registerWebRoutes({ register: ({ path, handler }) => { routes.set(path, handler); return () => {}; } });
    const call = async (method, body, origin = 'http://localhost:1234') => {
      const req = Readable.from(body === undefined ? [] : [JSON.stringify(body)]);
      req.method = method;
      req.headers = { host: 'localhost:1234', origin };
      let status;
      let data;
      await routes.get('/anisignal/updates')(req, {
        writeHead: (value) => { status = value; },
        end: (payload) => { data = JSON.parse(payload); },
      });
      assert.ok(!JSON.stringify(data).includes('private-token'));
      return { status, data };
    };
    assert.equal((await call('GET')).status, 200);
    assert.equal(checks.length, 1);
    assert.equal((await call('POST', { auto_check: false, revision: 'outdated' })).status, 409);
    assert.equal(service.config.auto_check_updates, true);
    const disabled = await call('POST', { auto_check: false });
    assert.equal(disabled.data.update.auto_check, false);
    assert.equal(disabled.data.revision, configurationRevision(service.config));
    assert.equal(checks.length, 1);
    const stored = JSON.parse(readFileSync(service.filePath, 'utf8'));
    assert.equal(stored.auto_check_updates, false);
    assert.equal(stored.tools_key, 'private-token');
    assert.equal(normalizeStoredConfig(stored).auto_check_updates, false);
    assert.equal(normalizeViewSubmission({ base_url: 'https://anisignal.xyz' }, stored).config.auto_check_updates, false);
    await call('GET');
    assert.equal(checks.length, 1);
    await call('POST', {});
    assert.deepEqual(checks.at(-1), { force: true });
    assert.equal((await call('POST', { auto_check: 'false' })).status, 400);
    assert.equal((await call('POST', {}, 'http://another-site.test')).status, 403);
    assert.equal((await call('DELETE')).status, 405);
    dispose();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
