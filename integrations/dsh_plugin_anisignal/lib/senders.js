/**
 * 双通道主动推送投递器（OutboundDispatcher）：
 *   - dsh_qqbot:{GroupMessage|FriendMessage}:<openid>  -> 腾讯官方 QQ Bot OpenAPI v2
 *   - dsh_onebot:{GroupMessage|FriendMessage}:<id>     -> OneBot v11 HTTP / 正向 WS / dsh-onebot-qq 工具兜底
 *
 * 投递结果严格区分：
 *   - accepted: 已确认送达
 *   - failed:   发送前失败或对端明确拒收（尚未向会话发出任何分片）
 *   - unknown:  请求超时、断连或多段消息部分送达，服务端不得重发以免刷屏
 */
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { resolveHomeDir } from './config.js';

const DEFAULT_TOKEN_BASE_URL = 'https://bots.qq.com';
const DEFAULT_API_BASE_URL = 'https://api.bot.qq.com';
const TOKEN_REFRESH_AHEAD_MS = 5 * 60_000;

function isPreConnectNetworkError(err) {
  const code = err?.cause?.code || err?.code || '';
  return code === 'ECONNREFUSED' || code === 'ENOTFOUND' || code === 'EAI_AGAIN' || code === 'ERR_INVALID_URL';
}

function parseUmo(umo) {
  const str = String(umo || '').trim();
  const idx1 = str.indexOf(':');
  const idx2 = idx1 >= 0 ? str.indexOf(':', idx1 + 1) : -1;
  if (idx1 <= 0 || idx2 <= idx1 + 1 || idx2 >= str.length - 1) return null;
  const platform = str.slice(0, idx1);
  const kind = str.slice(idx1 + 1, idx2);
  const targetId = str.slice(idx2 + 1);
  if (kind !== 'GroupMessage' && kind !== 'FriendMessage') return null;
  return { platform, kind, targetId, isGroup: kind === 'GroupMessage' };
}

// shortcut: 宿主未装 yaml 时用轻量行级扫描读取 cordis.patch.yml 标量键, 复杂多行锚点交给动态 import('yaml')
function scanPatchEntriesFallback(rawYaml) {
  const entries = [];
  let current = null;
  let inConfig = false;
  let configIndent = 0;

  for (const rawLine of String(rawYaml || '').split(/\r?\n/)) {
    if (!rawLine.trim() || rawLine.trim().startsWith('#')) continue;
    const topId = rawLine.match(/^\s*-\s+id:\s*['"]?([^'"#\s]+)['"]?/);
    if (topId) {
      current = { id: topId[1], disabled: false, config: {} };
      entries.push(current);
      inConfig = false;
      continue;
    }
    if (!current) continue;
    const indent = rawLine.match(/^(\s*)/)?.[1]?.length ?? 0;
    const trimmed = rawLine.trim();
    if (/^disabled:\s*true\b/i.test(trimmed)) {
      current.disabled = true;
      continue;
    }
    if (/^config:\s*$/.test(trimmed)) {
      inConfig = true;
      configIndent = indent;
      continue;
    }
    if (inConfig) {
      if (indent <= configIndent) {
        inConfig = false;
      } else {
        const kv = trimmed.match(/^([A-Za-z0-9_]+):\s*(.+)$/);
        if (kv) {
          let val = kv[2].trim();
          if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
            val = val.slice(1, -1);
          } else {
            val = val.replace(/\s+#.*$/, '').trim();
          }
          current.config[kv[1]] = val;
        }
      }
    }
  }
  return entries;
}

async function parseCordisPatchFile(filePath) {
  if (!filePath || !existsSync(filePath)) return [];
  let raw = '';
  try {
    raw = readFileSync(filePath, 'utf8');
  } catch {
    return [];
  }
  try {
    const yamlMod = await import('yaml');
    if (typeof yamlMod.parse === 'function') {
      const doc = yamlMod.parse(raw, {
        customTags: [{ tag: 'tag:yaml.org,2002:js', resolve: (v) => v }],
      });
      if (Array.isArray(doc)) {
        return doc.filter((item) => item && typeof item === 'object');
      }
    }
  } catch {
    /* fallback to lightweight scanner */
  }
  return scanPatchEntriesFallback(raw);
}

function listProfilePatchPaths(dshRoot) {
  const profilesDir = join(dshRoot, 'profiles');
  if (!existsSync(profilesDir)) return [];
  try {
    const names = readdirSync(profilesDir);
    const ordered = [
      ...names.filter((n) => n === 'desktop' || n === 'default'),
      ...names.filter((n) => n !== 'desktop' && n !== 'default'),
    ];
    return ordered
      .map((name) => join(profilesDir, name, 'cordis.patch.yml'))
      .filter((p) => existsSync(p));
  } catch {
    return [];
  }
}

export class OutboundDispatcher {
  constructor(ctx, options = {}) {
    this.ctx = ctx;
    this.fetchImpl = options.fetchImpl || globalThis.fetch;
    this.WebSocketImpl = options.WebSocketImpl || globalThis.WebSocket;
    this.dshRoot = options.dshRoot || dirname(resolveHomeDir(ctx));
    this.tokenBaseUrl = (options.tokenBaseUrl || process.env.QQBOT_TOKEN_URL || DEFAULT_TOKEN_BASE_URL).replace(/\/+$/, '');
    this.apiBaseUrl = (options.apiBaseUrl || process.env.QQBOT_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/+$/, '');
    this.sessionResolver = options.sessionResolver || null;
    this.#tokenCache = new Map(); // appId -> { token, expiresAt, secret }
  }

  #tokenCache;

  async resolveQqBotCredentials(config) {
    if (config?.qqbot_app_id && config?.qqbot_app_secret) {
      return {
        appId: config.qqbot_app_id,
        appSecret: config.qqbot_app_secret,
        source: 'plugin_config',
      };
    }
    if (process.env.QQBOT_APPID?.trim() && process.env.QQBOT_SECRET?.trim()) {
      return {
        appId: process.env.QQBOT_APPID.trim(),
        appSecret: process.env.QQBOT_SECRET.trim(),
        source: 'env',
      };
    }
    for (const patchPath of listProfilePatchPaths(this.dshRoot)) {
      const entries = await parseCordisPatchFile(patchPath);
      for (const entry of entries) {
        if (entry.disabled) continue;
        const isQqBot = entry.id === 'im-qqbot' || String(entry.path || '').includes('dsh-qqbot');
        if (!isQqBot) continue;
        const appId = String(entry.config?.appId || '').trim();
        const appSecret = String(entry.config?.appSecret || '').trim();
        if (appId && appSecret) {
          return { appId, appSecret, source: 'dsh_profile' };
        }
      }
    }
    return null;
  }

  async resolveOneBotEndpoint(config) {
    if (config?.onebot_http_url) {
      return {
        kind: 'http',
        url: config.onebot_http_url,
        accessToken: config.onebot_access_token || '',
        source: 'plugin_config',
      };
    }
    if (config?.onebot_ws_url) {
      return {
        kind: 'ws',
        url: config.onebot_ws_url,
        accessToken: config.onebot_access_token || '',
        source: 'plugin_config',
      };
    }

    const dshQqSettings = join(this.dshRoot, 'dsh-qq', 'settings.json');
    if (existsSync(dshQqSettings)) {
      try {
        const parsed = JSON.parse(readFileSync(dshQqSettings, 'utf8'));
        if (typeof parsed?.wsUrl === 'string' && /^wss?:\/\//i.test(parsed.wsUrl.trim())) {
          return {
            kind: 'ws',
            url: parsed.wsUrl.trim(),
            accessToken: String(parsed.accessToken || config?.onebot_access_token || '').trim(),
            source: 'dsh_qq_settings',
          };
        }
      } catch {
        /* ignore malformed settings.json */
      }
    }

    for (const patchPath of listProfilePatchPaths(this.dshRoot)) {
      const entries = await parseCordisPatchFile(patchPath);
      for (const entry of entries) {
        if (entry.disabled) continue;
        const cfg = entry.config || {};
        const idOrPath = `${entry.id || ''} ${entry.path || ''}`;
        if (idOrPath.includes('onebot') || idOrPath.includes('dsh-qq')) {
          if (typeof cfg.wsUrl === 'string' && /^wss?:\/\//i.test(cfg.wsUrl.trim())) {
            return {
              kind: 'ws',
              url: cfg.wsUrl.trim(),
              accessToken: String(cfg.accessToken || cfg.token || config?.onebot_access_token || '').trim(),
              source: 'dsh_profile',
            };
          }
          if (cfg.mode !== 'reverse' && typeof cfg.url === 'string' && /^wss?:\/\//i.test(cfg.url.trim())) {
            return {
              kind: 'ws',
              url: cfg.url.trim(),
              accessToken: String(cfg.token || cfg.accessToken || config?.onebot_access_token || '').trim(),
              source: 'dsh_profile',
            };
          }
        }
      }
    }

    if (this.sessionResolver?.liveOneBotAgent) {
      return {
        kind: 'live_tool',
        url: '',
        accessToken: '',
        source: 'dsh_onebot_agent',
      };
    }

    return null;
  }

  async inspectTransports(config) {
    const [qqbot, onebot] = await Promise.all([
      this.resolveQqBotCredentials(config),
      this.resolveOneBotEndpoint(config),
    ]);
    return {
      qqbot: qqbot
        ? { ready: true, source: qqbot.source, appIdTail: qqbot.appId.slice(-4) }
        : { ready: false, source: 'none', appIdTail: '' },
      onebot: onebot
        ? { ready: true, kind: onebot.kind, source: onebot.source, url: onebot.url }
        : { ready: false, kind: 'none', source: 'none', url: '' },
    };
  }

  async #getQqBotToken(appId, appSecret) {
    const cached = this.#tokenCache.get(appId);
    if (cached && cached.secret === appSecret && Date.now() < cached.expiresAt - TOKEN_REFRESH_AHEAD_MS) {
      return cached.token;
    }
    const resp = await this.fetchImpl(`${this.tokenBaseUrl}/app/getAppAccessToken`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ appId, clientSecret: appSecret }),
      signal: AbortSignal.timeout(10_000),
    });
    const text = await resp.text();
    let data = null;
    try {
      data = JSON.parse(text);
    } catch {
      /* non-json */
    }
    if (!resp.ok || !data?.access_token) {
      const detail = data?.message || data?.msg || text.slice(0, 160) || `HTTP ${resp.status}`;
      throw new Error(`获取 QQ Bot AccessToken 失败: ${detail}`);
    }
    const expiresSec = Math.max(60, Number(data.expires_in) || 7200);
    this.#tokenCache.set(appId, {
      token: String(data.access_token),
      expiresAt: Date.now() + expiresSec * 1000,
      secret: appSecret,
    });
    return String(data.access_token);
  }

  async #qqbotApiRequest(token, path, body, timeoutMs = 15_000) {
    const resp = await this.fetchImpl(`${this.apiBaseUrl}${path}`, {
      method: 'POST',
      headers: {
        Authorization: `QQBot ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await resp.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { raw: text };
    }
    if (!resp.ok) {
      const err = new Error(data?.message || `QQ OpenAPI HTTP ${resp.status}`);
      err.httpStatus = resp.status;
      throw err;
    }
    return data;
  }

  async #sendViaQqBot(target, parts, config) {
    const creds = await this.resolveQqBotCredentials(config);
    if (!creds) {
      return {
        status: 'failed',
        error: '未配置官方 QQ Bot 凭据（请在插件设置填写 AppID/AppSecret，或在 DSH 中绑定 @tencent-connect/dsh-qqbot）',
      };
    }

    let token;
    try {
      token = await this.#getQqBotToken(creds.appId, creds.appSecret);
    } catch (err) {
      return { status: 'failed', error: String(err?.message || err) };
    }

    const scopePath = target.isGroup ? `/v2/groups/${target.targetId}` : `/v2/users/${target.targetId}`;
    const steps = [];
    for (let i = 0; i < parts.length; i += 1) {
      const cur = parts[i];
      const next = parts[i + 1];
      if (
        cur.type === 'text'
        && !config?.qqbot_markdown
        && next
        && (next.type === 'image' || next.type === 'image_buffer')
      ) {
        steps.push({ kind: 'media', media: next, caption: cur.text.trim() });
        i += 1;
      } else if (cur.type === 'text') {
        if (cur.text.trim()) steps.push({ kind: 'text', text: cur.text.trim() });
      } else if (cur.type === 'image' || cur.type === 'image_buffer') {
        steps.push({ kind: 'media', media: cur, caption: '' });
      }
    }

    if (steps.length === 0) {
      return { status: 'accepted', error: '' };
    }

    let anySent = false;
    let msgSeq = 1;

    for (const step of steps) {
      if (step.kind === 'media') {
        let uploadRes;
        try {
          const uploadBody = step.media.type === 'image_buffer'
            ? { file_type: 1, srv_send_msg: false, file_data: step.media.buffer.toString('base64') }
            : { file_type: 1, srv_send_msg: false, url: step.media.url };
          uploadRes = await this.#qqbotApiRequest(token, `${scopePath}/files`, uploadBody, 25_000);
        } catch (err) {
          if (!anySent && err?.httpStatus === 401) {
            this.#tokenCache.delete(creds.appId);
          }
          return {
            status: anySent ? 'unknown' : 'failed',
            error: `媒体上传失败: ${err?.message || err}`,
          };
        }
        if (!uploadRes?.file_info) {
          return {
            status: anySent ? 'unknown' : 'failed',
            error: '媒体上传未返回 file_info',
          };
        }
        try {
          await this.#qqbotApiRequest(token, `${scopePath}/messages`, {
            msg_type: 7,
            media: { file_info: uploadRes.file_info },
            msg_seq: msgSeq++,
            ...(step.caption ? { content: step.caption } : {}),
          }, 15_000);
          anySent = true;
        } catch (err) {
          const definitePreSendReject = !anySent && (Boolean(err?.httpStatus) || isPreConnectNetworkError(err));
          return {
            status: definitePreSendReject ? 'failed' : 'unknown',
            error: `富媒体消息发送异常: ${err?.message || err}`,
          };
        }
      } else {
        const msgBody = config?.qqbot_markdown
          ? { markdown: { content: step.text }, msg_type: 2, msg_seq: msgSeq++ }
          : { content: step.text, msg_type: 0, msg_seq: msgSeq++ };
        try {
          await this.#qqbotApiRequest(token, `${scopePath}/messages`, msgBody, 15_000);
          anySent = true;
        } catch (err) {
          if (!anySent && err?.httpStatus === 401) {
            this.#tokenCache.delete(creds.appId);
          }
          const definitePreSendReject = !anySent && (Boolean(err?.httpStatus) || isPreConnectNetworkError(err));
          return {
            status: definitePreSendReject ? 'failed' : 'unknown',
            error: `文本消息发送异常: ${err?.message || err}`,
          };
        }
      }
    }

    return { status: 'accepted', error: '' };
  }

  #buildOneBotSegments(parts) {
    const segments = [];
    for (const part of parts) {
      if (part.type === 'text' && part.text) {
        segments.push({ type: 'text', data: { text: part.text } });
      } else if (part.type === 'image' && part.url) {
        segments.push({ type: 'image', data: { file: part.url } });
      } else if (part.type === 'image_buffer' && part.buffer) {
        segments.push({ type: 'image', data: { file: `base64://${part.buffer.toString('base64')}` } });
      }
    }
    return segments;
  }

  async #sendViaOneBotHttp(endpoint, target, segments) {
    const action = target.isGroup ? 'send_group_msg' : 'send_private_msg';
    const idField = target.isGroup ? 'group_id' : 'user_id';
    const numericId = /^\d+$/.test(target.targetId) ? Number(target.targetId) : target.targetId;
    const headers = { 'Content-Type': 'application/json' };
    if (endpoint.accessToken) {
      headers.Authorization = `Bearer ${endpoint.accessToken}`;
    }

    let resp;
    try {
      resp = await this.fetchImpl(`${endpoint.url.replace(/\/+$/, '')}/${action}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ [idField]: numericId, message: segments }),
        signal: AbortSignal.timeout(20_000),
      });
    } catch (err) {
      return {
        status: isPreConnectNetworkError(err) ? 'failed' : 'unknown',
        error: `OneBot HTTP 请求异常: ${err?.message || err}`,
      };
    }

    const raw = await resp.text();
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      data = null;
    }
    if (!resp.ok) {
      return { status: 'failed', error: `OneBot HTTP ${resp.status}: ${raw.slice(0, 160)}` };
    }
    if (data && data.status === 'failed') {
      return { status: 'failed', error: `OneBot 拒收 (retcode=${data.retcode}): ${data.wording || data.message || ''}` };
    }
    if (data && typeof data.retcode === 'number' && data.retcode !== 0) {
      return { status: 'failed', error: `OneBot 返回错误码 ${data.retcode}: ${data.wording || data.message || ''}` };
    }
    return { status: 'accepted', error: '' };
  }

  async #sendViaOneBotWs(endpoint, target, segments) {
    if (typeof this.WebSocketImpl !== 'function') {
      return { status: 'failed', error: '当前 Node 运行时不支持内置 WebSocket，请配置 OneBot HTTP 地址' };
    }
    const action = target.isGroup ? 'send_group_msg' : 'send_private_msg';
    const idField = target.isGroup ? 'group_id' : 'user_id';
    const numericId = /^\d+$/.test(target.targetId) ? Number(target.targetId) : target.targetId;
    const echo = `anisignal_${Date.now()}_${randomBytes(4).toString('hex')}`;

    let wsUrl = endpoint.url;
    if (endpoint.accessToken && !/[?&]access_token=/i.test(wsUrl)) {
      wsUrl += `${wsUrl.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(endpoint.accessToken)}`;
    }

    return new Promise((resolveOutcome) => {
      let frameSent = false;
      let settled = false;
      let ws;
      const finish = (outcome) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
        resolveOutcome(outcome);
      };

      const timer = setTimeout(() => {
        finish({
          status: frameSent ? 'unknown' : 'failed',
          error: frameSent ? 'OneBot WS 等待响应超时' : 'OneBot WS 连接超时',
        });
      }, 15_000);

      try {
        ws = new this.WebSocketImpl(wsUrl);
      } catch (err) {
        finish({ status: 'failed', error: `OneBot WS 地址无效: ${err?.message || err}` });
        return;
      }

      ws.addEventListener('open', () => {
        try {
          ws.send(JSON.stringify({
            action,
            params: { [idField]: numericId, message: segments },
            echo,
          }));
          frameSent = true;
        } catch (err) {
          finish({ status: 'failed', error: `OneBot WS 发送帧失败: ${err?.message || err}` });
        }
      });

      ws.addEventListener('message', (ev) => {
        try {
          const text = typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data).toString('utf8');
          const msg = JSON.parse(text);
          if (msg?.echo !== echo) return;
          if (msg.status === 'ok' || msg.retcode === 0) {
            finish({ status: 'accepted', error: '' });
          } else {
            finish({
              status: 'failed',
              error: `OneBot WS 拒收 (retcode=${msg.retcode}): ${msg.wording || msg.message || ''}`,
            });
          }
        } catch {
          /* ignore unrelated frames */
        }
      });

      ws.addEventListener('error', (ev) => {
        finish({
          status: frameSent ? 'unknown' : 'failed',
          error: `OneBot WS 连接错误: ${ev?.message || ev?.error?.message || 'socket error'}`,
        });
      });

      ws.addEventListener('close', () => {
        if (!settled) {
          finish({
            status: frameSent ? 'unknown' : 'failed',
            error: frameSent ? 'OneBot WS 在收到回执前关闭' : 'OneBot WS 握手被关闭',
          });
        }
      });
    });
  }

  async #resolveLiveOneBotTools(agent) {
    if (!agent) return null;
    try {
      const presets = this.ctx?.get?.('agentPresets');
      if (typeof presets?.serviceFor === 'function') {
        const svc = presets.serviceFor(agent, 'tools');
        if (svc) return svc;
      }
    } catch {
      /* fallback */
    }
    try {
      const mod = await import('@deepseek-ai/dsh-agent-presets');
      if (typeof mod?.serviceForAgent === 'function') {
        const svc = mod.serviceForAgent(this.ctx, agent, 'tools');
        if (svc) return svc;
      }
    } catch {
      /* fallback */
    }
    return this.ctx?.get?.('tools') || null;
  }

  #lookupTool(toolsSvc, toolName, agent) {
    if (!toolsSvc) return null;
    for (const method of ['get', 'resolve', 'find']) {
      if (typeof toolsSvc[method] === 'function') {
        try {
          const found = toolsSvc[method](toolName, agent) ?? toolsSvc[method](toolName);
          if (found?.execute) return found;
        } catch {
          /* ignore */
        }
      }
    }
    if (typeof toolsSvc.list === 'function') {
      try {
        const list = toolsSvc.list(agent) ?? toolsSvc.list();
        if (Array.isArray(list)) {
          const hit = list.find((t) => t?.name === toolName && typeof t.execute === 'function');
          if (hit) return hit;
        }
      } catch {
        /* ignore */
      }
    }
    return null;
  }

  async #sendViaLiveOneBotTools(target, parts) {
    const agent = this.sessionResolver?.liveOneBotAgent;
    const toolsSvc = await this.#resolveLiveOneBotTools(agent);
    const sendTextTool = this.#lookupTool(toolsSvc, 'qq_send_text', agent);
    const sendImageTool = this.#lookupTool(toolsSvc, 'qq_send_image', agent);
    if (!sendTextTool && !sendImageTool) {
      return {
        status: 'failed',
        error: '未配置 OneBot HTTP/WS 地址，且当前无可用的 dsh-onebot 工具实例',
      };
    }

    const chatId = `${target.isGroup ? 'group' : 'private'}:${target.targetId}`;
    const mediaDir = join(this.dshRoot, 'media', 'onebot');
    const stagedFiles = [];
    let anySent = false;

    try {
      for (let i = 0; i < parts.length; i += 1) {
        const cur = parts[i];
        const next = parts[i + 1];
        if (
          cur.type === 'text'
          && next
          && (next.type === 'image' || next.type === 'image_buffer')
          && sendImageTool
        ) {
          const src = this.#stageImageSource(next, mediaDir, stagedFiles);
          await sendImageTool.execute({ chat_id: chatId, sources: [src], caption: cur.text.trim() }, {});
          anySent = true;
          i += 1;
        } else if (cur.type === 'text' && cur.text.trim()) {
          if (!sendTextTool) throw new Error('缺少 qq_send_text 工具');
          await sendTextTool.execute({ chat_id: chatId, text: cur.text.trim() }, {});
          anySent = true;
        } else if (cur.type === 'image' || cur.type === 'image_buffer') {
          if (!sendImageTool) throw new Error('缺少 qq_send_image 工具');
          const src = this.#stageImageSource(cur, mediaDir, stagedFiles);
          await sendImageTool.execute({ chat_id: chatId, sources: [src] }, {});
          anySent = true;
        }
      }
      return { status: 'accepted', error: '' };
    } catch (err) {
      return {
        status: anySent ? 'unknown' : 'failed',
        error: `OneBot 工具发送失败: ${err?.message || err}`,
      };
    } finally {
      for (const file of stagedFiles) {
        try {
          unlinkSync(file);
        } catch {
          /* ignore */
        }
      }
    }
  }

  #stageImageSource(part, mediaDir, stagedFiles) {
    if (part.type === 'image') return part.url;
    mkdirSync(mediaDir, { recursive: true });
    const filePath = join(mediaDir, `anisignal-${Date.now()}-${randomBytes(4).toString('hex')}.png`);
    writeFileSync(filePath, part.buffer);
    stagedFiles.push(filePath);
    return filePath;
  }

  async #sendViaOneBot(target, parts, config) {
    const endpoint = await this.resolveOneBotEndpoint(config);
    if (!endpoint) {
      return {
        status: 'failed',
        error: '未找到可用的 OneBot v11 连接（请在插件设置填写 NapCat HTTP 或正向 WS 地址）',
      };
    }
    const segments = this.#buildOneBotSegments(parts);
    if (segments.length === 0) {
      return { status: 'accepted', error: '' };
    }
    if (endpoint.kind === 'http') {
      return this.#sendViaOneBotHttp(endpoint, target, segments);
    }
    if (endpoint.kind === 'ws') {
      return this.#sendViaOneBotWs(endpoint, target, segments);
    }
    return this.#sendViaLiveOneBotTools(target, parts);
  }

  async deliver(umo, parts, config) {
    const target = parseUmo(umo);
    if (!target) {
      return { status: 'failed', error: `无效的会话标识: ${umo}` };
    }
    if (target.platform === 'dsh_qqbot') {
      return this.#sendViaQqBot(target, parts, config);
    }
    if (target.platform === 'dsh_onebot') {
      return this.#sendViaOneBot(target, parts, config);
    }
    return {
      status: 'failed',
      error: `不支持的推送通道前缀 "${target.platform}"（仅支持 dsh_qqbot 与 dsh_onebot）`,
    };
  }
}
