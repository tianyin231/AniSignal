/**
 * 从 DSH 会话实例与插件持久化账本还原可信会话标识（umo）与发言人身份（actorId）。
 *
 * 支持三种主流 DSH QQ 接入插件：
 *   1. @tencent-connect/dsh-qqbot (im-qqbot)  -> dsh_qqbot:{GroupMessage|FriendMessage}:<openid>
 *   2. dsh-onebot-qq (dsh-onebot)             -> dsh_onebot:{GroupMessage|FriendMessage}:<qq_or_group>
 *   3. dsh-qq-onebot (dsh-qq)                 -> dsh_onebot:{GroupMessage|FriendMessage}:<qq_or_group>
 *
 * 安全边界：umo 仅由宿主 sessionId 与插件本地状态文件反查，绝不接受模型参数覆盖；
 * 群聊 actorId 仅从各插件受保护的首部信封提取，拒绝匹配正文内伪造的标签。
 */
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { resolveHomeDir } from './config.js';

const ONEBOT_SESSION_RE = /^onebot-(private|group)-(\d+)(?:-[A-Za-z0-9]+)?$/;
const QQBOT_KEY_RE = /^qqbot:([^:]+):(c2c|group):([^:]+)$/;
const DSH_QQ_KEY_RE = /^qq:([^:]+):(private|group):([^:@#]+)(?:@iso)?(?:#\d+)?$/;

export function extractSessionId(sessionOrAgent) {
  if (!sessionOrAgent || typeof sessionOrAgent !== 'object') return '';
  const session = sessionOrAgent.session && typeof sessionOrAgent.session === 'object'
    ? sessionOrAgent.session
    : sessionOrAgent;
  const raw = session?.header?.id
    ?? session?.id
    ?? sessionOrAgent?.sessionId
    ?? '';
  return typeof raw === 'string' ? raw.trim() : '';
}

export function readSessionEvents(session) {
  if (!session || typeof session !== 'object') return [];
  if (typeof session.snapshotEvents === 'function') {
    try {
      const snap = session.snapshotEvents();
      return Array.isArray(snap) ? snap : [];
    } catch {
      return [];
    }
  }
  return Array.isArray(session.events) ? session.events : [];
}

export function extractEventText(event) {
  const content = event?.data?.message?.content
    ?? event?.message?.content
    ?? event?.data?.content
    ?? event?.content;
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((part) => (part && part.type === 'text' && typeof part.text === 'string' ? part.text : ''))
    .filter(Boolean)
    .join('\n');
}

export function parseOneBotQqGroupEnvelope(rawText) {
  const text = String(rawText ?? '');
  if (!text) return { actorId: '', mid: '' };
  const nl = text.indexOf('\n');
  const firstLine = nl >= 0 ? text.slice(0, nl) : text;
  const tagMatch = firstLine.match(/<user_message\s+qq="(\d+)"\s+nickname="[^"]*"(?:\s+mid="([^"]*)")?>$/);
  if (tagMatch) {
    return { actorId: tagMatch[1], mid: tagMatch[2] || '' };
  }
  const legacyMatch = firstLine.match(/^(?:\[受限用户:[^\]]+\]\s*)?\[\d{2}:\d{2}\s+[^\n()]*?\((\d+)\)·(?:管理员|成员)\]/);
  return { actorId: legacyMatch ? legacyMatch[1] : '', mid: '' };
}

export function parseDshQqGroupEnvelope(rawText) {
  let text = String(rawText ?? '');
  if (!text) return { actorId: '' };
  const histEnd = '\n[群聊最近消息结束]\n';
  const histIdx = text.indexOf(histEnd);
  if (histIdx >= 0 && text.includes('[群聊最近消息]')) {
    text = text.slice(histIdx + histEnd.length);
  }
  let matchedHeader = '';
  for (const m of text.matchAll(/^⟨([^⟩\n]+)⟩/gm)) {
    matchedHeader = m[1];
  }
  if (!matchedHeader) return { actorId: '' };
  const strict = matchedHeader.match(/\((\d+)\)(?: · [^⟩]+)? \d{2}-\d{2} 周[日一二三四五六] \d{2}:\d{2}（[^）]+）$/);
  if (strict) return { actorId: strict[1] };
  const fallback = matchedHeader.match(/\((\d+)\)[^()]*$/);
  return { actorId: fallback ? fallback[1] : '' };
}

export function parseQqBotGroupEnvelope(rawText) {
  let text = String(rawText ?? '');
  if (!text) return { actorId: '' };

  if (text.startsWith('[Chat history begins]\n')) {
    const marker = '\n\n[Chat history ends]\n[Current message]\n';
    const idx = text.indexOf(marker);
    if (idx < 0) return { actorId: '' };
    text = text.slice(idx + marker.length);
  }

  // 跳过 buildDynamicCtx 生成的附件与语音元数据头
  const lines = text.split('\n');
  let i = 0;
  let inRefAttachments = false;
  while (i < lines.length) {
    const line = lines[i];
    if (line === '') {
      i += 1;
      inRefAttachments = false;
      continue;
    }
    if (line === '[Reference attachments]') {
      inRefAttachments = true;
      i += 1;
      continue;
    }
    if (inRefAttachments && line.startsWith('  - ')) {
      i += 1;
      continue;
    }
    if (/^- (?:Image|Video|File|ASR|Voice): /.test(line)) {
      i += 1;
      continue;
    }
    break;
  }
  text = lines.slice(i).join('\n');

  if (text.startsWith('[Quoted message begins]\n')) {
    const qMarker = '\n[Quoted message ends]\n[Current message]\n';
    const qIdx = text.indexOf(qMarker);
    if (qIdx < 0) return { actorId: '' };
    text = text.slice(qIdx + qMarker.length);
  }

  const firstLine = text.split('\n', 1)[0] || '';
  const senderMatch = firstLine.match(/^\[[^\n\]]*\(([^()\s\]]+)\)\](?=\s|$)/);
  return { actorId: senderMatch ? senderMatch[1] : '' };
}

function loadJsonCached(filePath, cacheSlot) {
  if (!filePath || !existsSync(filePath)) {
    cacheSlot.mtimeMs = -1;
    cacheSlot.data = null;
    return null;
  }
  try {
    const mtimeMs = statSync(filePath).mtimeMs;
    if (cacheSlot.mtimeMs === mtimeMs && cacheSlot.data !== undefined) {
      return cacheSlot.data;
    }
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    cacheSlot.mtimeMs = mtimeMs;
    cacheSlot.data = parsed && typeof parsed === 'object' ? parsed : null;
    return cacheSlot.data;
  } catch {
    return cacheSlot.data ?? null;
  }
}

export class SessionResolver {
  constructor(ctx, options = {}) {
    this.ctx = ctx;
    const dshRoot = dirname(resolveHomeDir(ctx));
    this.qqbotPrefsPath = options.qqbotPrefsPath
      || resolve(homedir() || '/tmp', '.dsh-qqbot', 'model-prefs.json');
    this.dshQqStatePath = options.dshQqStatePath
      || join(dshRoot, 'dsh-qq', 'session-state.json');

    this.#qqbotCache = { mtimeMs: -1, data: null };
    this.#dshQqCache = { mtimeMs: -1, data: null };

    this.lastActivity = new Map(); // umo -> ISO UTC string
    this.recentSessions = new Map(); // umo -> { umo, channel, plugin, scope, peerId, lastSeenAt }
    this.lastUserEventBySession = new Map(); // sessionId -> { text, seq }
    this.liveOneBotAgent = null;
  }

  #qqbotCache;
  #dshQqCache;

  resolvePeerBySessionId(sessionId) {
    const sid = String(sessionId || '').trim();
    if (!sid) return null;

    const obMatch = sid.match(ONEBOT_SESSION_RE);
    if (obMatch) {
      const scope = obMatch[1] === 'group' ? 'group' : 'private';
      const peerId = obMatch[2];
      return {
        sessionId: sid,
        channel: 'dsh_onebot',
        plugin: 'dsh-onebot-qq',
        scope,
        peerId,
        umo: `dsh_onebot:${scope === 'group' ? 'GroupMessage' : 'FriendMessage'}:${peerId}`,
      };
    }

    const qqbotPrefs = loadJsonCached(this.qqbotPrefsPath, this.#qqbotCache);
    if (qqbotPrefs?.sessionIds && typeof qqbotPrefs.sessionIds === 'object') {
      for (const [key, mappedSid] of Object.entries(qqbotPrefs.sessionIds)) {
        if (mappedSid !== sid) continue;
        const m = key.match(QQBOT_KEY_RE);
        if (!m) continue;
        const scope = m[2] === 'group' ? 'group' : 'c2c';
        const peerId = m[3];
        return {
          sessionId: sid,
          channel: 'dsh_qqbot',
          plugin: 'im-qqbot',
          appId: m[1],
          scope,
          peerId,
          umo: `dsh_qqbot:${scope === 'group' ? 'GroupMessage' : 'FriendMessage'}:${peerId}`,
        };
      }
    }

    const dshQqState = loadJsonCached(this.dshQqStatePath, this.#dshQqCache);
    if (dshQqState?.sessions && typeof dshQqState.sessions === 'object') {
      for (const [key, mappedSid] of Object.entries(dshQqState.sessions)) {
        if (mappedSid !== sid) continue;
        const m = key.match(DSH_QQ_KEY_RE);
        if (!m) continue;
        const scope = m[2] === 'group' ? 'group' : 'private';
        const peerId = m[3];
        return {
          sessionId: sid,
          channel: 'dsh_onebot',
          plugin: 'dsh-qq',
          selfId: m[1],
          scope,
          peerId,
          umo: `dsh_onebot:${scope === 'group' ? 'GroupMessage' : 'FriendMessage'}:${peerId}`,
        };
      }
    }

    return null;
  }

  #latestUserMessage(session, sessionId) {
    const events = readSessionEvents(session);
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const ev = events[i];
      if (ev?.type === 'user/message') {
        const text = extractEventText(ev);
        if (text) return { text, seq: ev.seq };
      }
    }
    return this.lastUserEventBySession.get(sessionId) || { text: '', seq: undefined };
  }

  resolve(agentOrSession) {
    const session = agentOrSession?.session && typeof agentOrSession.session === 'object'
      ? agentOrSession.session
      : agentOrSession;
    const sessionId = extractSessionId(agentOrSession);
    const peer = this.resolvePeerBySessionId(sessionId);
    if (!peer) return null;

    const latest = this.#latestUserMessage(session, sessionId);
    let actorId = '';
    let mid = '';

    if (peer.scope !== 'group') {
      actorId = peer.peerId;
    } else if (peer.plugin === 'dsh-onebot-qq') {
      const parsed = parseOneBotQqGroupEnvelope(latest.text);
      actorId = parsed.actorId;
      mid = parsed.mid;
    } else if (peer.plugin === 'dsh-qq') {
      actorId = parseDshQqGroupEnvelope(latest.text).actorId;
    } else if (peer.plugin === 'im-qqbot') {
      actorId = parseQqBotGroupEnvelope(latest.text).actorId;
    }

    const turnToken = mid
      || (latest.seq !== undefined ? `seq-${latest.seq}` : createHash('sha1').update(latest.text || sessionId).digest('hex').slice(0, 12));

    return {
      ...peer,
      actorId,
      requestId: `${sessionId}:${turnToken}`,
    };
  }

  noteSessionEvent(session, event, agent = null) {
    const sessionId = extractSessionId(session || agent);
    if (!sessionId) return null;

    if (event?.type === 'user/message') {
      const text = extractEventText(event);
      if (text) {
        this.lastUserEventBySession.set(sessionId, { text, seq: event.seq });
        if (this.lastUserEventBySession.size > 500) {
          const oldest = this.lastUserEventBySession.keys().next().value;
          if (oldest) this.lastUserEventBySession.delete(oldest);
        }
      }
    }

    const peer = this.resolvePeerBySessionId(sessionId);
    if (!peer) return null;

    if (agent && peer.plugin === 'dsh-onebot-qq') {
      this.liveOneBotAgent = agent;
    }

    if (event?.type === 'user/message') {
      const iso = new Date().toISOString();
      this.lastActivity.set(peer.umo, iso);
      this.recentSessions.set(peer.umo, {
        umo: peer.umo,
        channel: peer.channel,
        plugin: peer.plugin,
        scope: peer.scope,
        peerId: peer.peerId,
        lastSeenAt: iso,
      });
    }
    return peer;
  }

  drainActivityPayload() {
    if (this.lastActivity.size === 0) return [];
    const items = [];
    for (const [umo, last_active_at] of this.lastActivity.entries()) {
      items.push({ umo, last_active_at });
    }
    return items;
  }

  ackActivityPayload(sentItems) {
    for (const item of sentItems) {
      if (this.lastActivity.get(item.umo) === item.last_active_at) {
        this.lastActivity.delete(item.umo);
      }
    }
  }

  discoverKnownSessions() {
    const map = new Map();
    for (const [umo, item] of this.recentSessions.entries()) {
      map.set(umo, { ...item });
    }

    const qqbotPrefs = loadJsonCached(this.qqbotPrefsPath, this.#qqbotCache);
    if (qqbotPrefs?.sessionIds && typeof qqbotPrefs.sessionIds === 'object') {
      for (const key of Object.keys(qqbotPrefs.sessionIds)) {
        const m = key.match(QQBOT_KEY_RE);
        if (!m) continue;
        const scope = m[2] === 'group' ? 'group' : 'c2c';
        const peerId = m[3];
        const umo = `dsh_qqbot:${scope === 'group' ? 'GroupMessage' : 'FriendMessage'}:${peerId}`;
        if (!map.has(umo)) {
          map.set(umo, { umo, channel: 'dsh_qqbot', plugin: 'im-qqbot', scope, peerId, lastSeenAt: '' });
        }
      }
    }

    const dshQqState = loadJsonCached(this.dshQqStatePath, this.#dshQqCache);
    if (dshQqState?.sessions && typeof dshQqState.sessions === 'object') {
      for (const key of Object.keys(dshQqState.sessions)) {
        const m = key.match(DSH_QQ_KEY_RE);
        if (!m) continue;
        const scope = m[2] === 'group' ? 'group' : 'private';
        const peerId = m[3];
        const umo = `dsh_onebot:${scope === 'group' ? 'GroupMessage' : 'FriendMessage'}:${peerId}`;
        if (!map.has(umo)) {
          map.set(umo, { umo, channel: 'dsh_onebot', plugin: 'dsh-qq', scope, peerId, lastSeenAt: '' });
        }
      }
    }

    return [...map.values()];
  }
}
