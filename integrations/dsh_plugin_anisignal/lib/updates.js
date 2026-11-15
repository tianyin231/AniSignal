import { VERSION } from './config.js';

export const UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000;
const REPOSITORY = 'tianyin231/DSH_Plugin_AniSignal';

function newerVersion(candidate, installed) {
  const next = candidate.split('.').map(Number);
  const current = installed.split('.').map(Number);
  for (let i = 0; i < 3; i += 1) {
    if (next[i] !== current[i]) return next[i] > current[i];
  }
  return false;
}

export class PluginUpdateChecker {
  constructor({ fetchImpl = globalThis.fetch, now = Date.now, currentVersion = VERSION } = {}) {
    this.fetchImpl = fetchImpl;
    this.now = now;
    this.currentVersion = currentVersion;
    this.lastAttempt = null;
    this.pending = null;
    this.state = {
      current_version: currentVersion,
      latest_version: '',
      available: false,
      checked_at: '',
      error: '',
      spec: '',
    };
  }

  snapshot() {
    return { ...this.state, checking: Boolean(this.pending) };
  }

  async getJson(url) {
    // GitHub 版本检查独立请求，不携带 AniSignal Token 或 QQ 凭据。
    const response = await this.fetchImpl(url, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`GitHub 请求失败：HTTP ${response.status}`);
    return response.json();
  }

  async check({ force = false } = {}) {
    if (this.pending) {
      await this.pending;
      return this.snapshot();
    }
    if (!force && this.lastAttempt !== null && this.now() - this.lastAttempt < UPDATE_INTERVAL_MS) {
      return this.snapshot();
    }
    this.lastAttempt = this.now();
    this.pending = this.performCheck();
    try {
      await this.pending;
    } finally {
      this.pending = null;
    }
    return this.snapshot();
  }

  async performCheck() {
    try {
      const { sha } = await this.getJson(`https://api.github.com/repos/${REPOSITORY}/commits/main`);
      if (!/^[a-f0-9]{40}$/i.test(sha || '')) throw new Error('GitHub 未返回有效提交版本');
      // 清单和安装目标锁定同一个提交，避免检查后 main 又变化而装错版本。
      const manifest = await this.getJson(`https://raw.githubusercontent.com/${REPOSITORY}/${sha}/package.json`);
      if (manifest.name !== 'dsh-plugin-anisignal' || !manifest.dsh?.bundle || !/^\d+\.\d+\.\d+$/.test(manifest.version || '')) {
        throw new Error('发布仓库的插件版本信息无效');
      }
      this.state = {
        current_version: this.currentVersion,
        latest_version: manifest.version,
        available: newerVersion(manifest.version, this.currentVersion),
        checked_at: new Date(this.now()).toISOString(),
        error: '',
        spec: `github:${REPOSITORY}#${sha}`,
      };
    } catch (err) {
      this.state = { ...this.state, checked_at: new Date(this.now()).toISOString(), error: String(err?.message || err) };
    }
  }
}
