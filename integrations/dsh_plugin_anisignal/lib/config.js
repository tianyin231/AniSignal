/**
 * AniSignal DSH 插件配置归一化、脱敏与服务端目标结构转换。
 * 与 integrations/astrbot_plugin_anisignal/configuration.py 保持契约一致。
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export const VERSION = '1.14.7';
export const UMO_PATTERN = /^[^:]+:(FriendMessage|GroupMessage):[^:]+$/;
export const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

export const DEFAULT_COMMUNITIES = [
  'wuwa', 'blhx', 'hsr', 'arknights', 'genshin', 'endfield', 'ai',
];

export const ALL_COMMUNITIES = [
  { id: 'wuwa', label: '鸣潮' },
  { id: 'blhx', label: '碧蓝航线' },
  { id: 'hsr', label: '崩坏星穹铁道' },
  { id: 'arknights', label: '明日方舟' },
  { id: 'genshin', label: '原神' },
  { id: 'endfield', label: '终末地' },
  { id: 'zzz', label: '绝区零' },
  { id: 'petitplanet', label: '星布谷地' },
  { id: 'hna', label: '崩坏：因缘精灵' },
  { id: 'roco', label: '洛克王国：世界' },
  { id: 'promilia', label: '蓝色星原：旅谣' },
  { id: 'nte', label: '异环' },
  { id: 'nikke', label: 'NIKKE' },
  { id: 'infinitynikki', label: '无限暖暖' },
  { id: 'ai', label: 'AI' },
];

export const QUERY_TOOLS = [
  { id: 'digest', label: '今日速报' },
  { id: 'news', label: '资讯查询' },
  { id: 'products', label: '商品进展' },
  { id: 'trending', label: '热度飙升' },
  { id: 'calendar', label: '活动日历' },
  { id: 'codes', label: '兑换码' },
];

export const CALENDAR_KINDS = [
  { id: 'activity', label: '活动结束' },
  { id: 'pool', label: '卡池结束' },
  { id: 'maintenance', label: '维护开始' },
  { id: 'deadline', label: '报名 / 兑换截止' },
];

const TOOL_IDS = new Set(QUERY_TOOLS.map((t) => t.id));
const CALENDAR_KIND_IDS = new Set(CALENDAR_KINDS.map((k) => k.id));
const SCOPE_MODES = new Set(['channels', 'legacy', 'custom']);
const DAILY_STYLES = new Set(['compact', 'split']);
const NOTIFY_STYLES = new Set(['persona', 'original']);
const NOTIFY_FORMATS = new Set(['card', 'text']);

export function defaultChannelsCatalog() {
  const out = {};
  for (const c of ALL_COMMUNITIES) {
    if (c.id === 'ai') {
      out['ai:news'] = 'AI · 快讯';
      continue;
    }
    const platforms = [
      ['bilibili', 'B站'],
      ['youtube', 'YouTube'],
      ['x', 'X'],
      ['web', '官网'],
      ['miyoushe', '米游社'],
    ];
    for (const [role, roleLabel] of [['official', '官方'], ['community', '社区']]) {
      for (const [pf, pfLabel] of platforms) {
        out[`${c.id}:${role}:${pf}`] = `${c.label} · ${roleLabel} · ${pfLabel}`;
      }
    }
    out[`${c.id}:product`] = `${c.label} · 商品`;
  }
  return out;
}

export function formatChannelLabel(raw) {
  return String(raw || '')
    .replace(/_/g, ' · ')
    .replace(/ · YB$/, ' · YouTube')
    .replace(/ · B$/, ' · B站');
}

export function defaultTarget(overrides = {}) {
  return {
    name: '',
    umo: '',
    enabled: false,
    subscription: {
      scope_mode: 'channels',
      inherit_preferences: true,
      inherit_keywords: false,
      communities: [...DEFAULT_COMMUNITIES],
      channels: [],
      custom_feeds: [],
      custom_feed_ids: [],
      keywords: [],
      exclude_keywords: [],
      ...(overrides.subscription || {}),
    },
    notifications: {
      daily_enabled: true,
      daily_sources: [],
      daily_time: '08:30',
      updates_enabled: true,
      updates_sources: [],
      calendar_enabled: true,
      calendar_kinds: ['activity', 'pool', 'maintenance', 'deadline'],
      calendar_time: '20:00',
      quiet_start: 23,
      quiet_end: 8,
      quiet_critical: false,
      merge_minutes: 0,
      min_push_interval_minutes: 0,
      daily_style: 'compact',
      max_images: 1,
      ...(overrides.notifications || {}),
    },
    presentation: {
      notification_style: 'persona',
      notification_format: 'card',
      instructions: '',
      notification_provider_id: '',
      notification_template: '{intro}\n\n{body}',
      notification_item_template: '{index}. {title}\n{summary}\n发布时间：{time}\n来源：{source}\n{url}\n{images}',
      show_details_hint: false,
      ...(overrides.presentation || {}),
    },
    interaction: {
      tools: ['digest', 'news', 'products'],
      chat_controls: false,
      manager_ids: [],
      reminders_enabled: false,
      activities_enabled: false,
      ...(overrides.interaction || {}),
    },
    ...(overrides.name !== undefined ? { name: overrides.name } : {}),
    ...(overrides.umo !== undefined ? { umo: overrides.umo } : {}),
    ...(overrides.enabled !== undefined ? { enabled: overrides.enabled } : {}),
  };
}

export function defaultConfig() {
  return {
    base_url: 'https://anisignal.xyz',
    tools_key: '',
    card_font_path: '',
    configuration_imported: false,
    qqbot_app_id: '',
    qqbot_app_secret: '',
    qqbot_markdown: false,
    onebot_http_url: '',
    onebot_ws_url: '',
    onebot_access_token: '',
    targets: [],
  };
}

export function resolveHomeDir(ctx) {
  try {
    const p = ctx?.get?.('dshHomePath');
    if (typeof p === 'function') {
      const v = p('anisignal');
      if (v) return String(v);
    } else if (typeof p === 'string' && p.trim()) {
      return join(p.trim(), 'anisignal');
    }
  } catch {
    /* fallback to env / ~/.dsh */
  }
  const root = process.env.DSH_HOME?.trim() || join(homedir() || '/tmp', '.dsh');
  return join(root, 'anisignal');
}

export function configFilePath(ctx) {
  return join(resolveHomeDir(ctx), 'config.json');
}

function uniqueStrings(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  const out = [];
  for (const item of list) {
    if (typeof item !== 'string') continue;
    const s = item.trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

function uniqueInts(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  const out = [];
  for (const item of list) {
    const n = typeof item === 'number' ? item : Number(String(item).trim());
    if (!Number.isInteger(n) || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }
  return out;
}

function clampInt(val, def, min, max) {
  const n = typeof val === 'number' ? val : Number(val);
  if (!Number.isInteger(n)) return def;
  if (n < min) return min;
  if (max !== undefined && n > max) return max;
  return n;
}

export function normalizeStoredTarget(raw) {
  const r = raw && typeof raw === 'object' ? raw : {};
  const base = defaultTarget();
  const sub = r.subscription && typeof r.subscription === 'object' ? r.subscription : {};
  const notif = r.notifications && typeof r.notifications === 'object' ? r.notifications : {};
  const pres = r.presentation && typeof r.presentation === 'object' ? r.presentation : {};
  const inter = r.interaction && typeof r.interaction === 'object' ? r.interaction : {};

  const channels = uniqueStrings(sub.channels ?? r.channels ?? []);
  const scopeModeRaw = sub.scope_mode ?? r.scope_mode;
  const scope_mode = SCOPE_MODES.has(scopeModeRaw)
    ? scopeModeRaw
    : (channels.length > 0 ? 'channels' : 'legacy');

  return {
    name: typeof r.name === 'string' ? r.name.trim() : '',
    umo: typeof r.umo === 'string' ? r.umo.trim() : '',
    enabled: Boolean(r.enabled),
    subscription: {
      scope_mode,
      inherit_preferences: typeof (sub.inherit_preferences ?? r.inherit_preferences) === 'boolean'
        ? Boolean(sub.inherit_preferences ?? r.inherit_preferences)
        : base.subscription.inherit_preferences,
      inherit_keywords: Boolean(sub.inherit_keywords ?? r.inherit_keywords ?? false),
      communities: uniqueStrings(sub.communities ?? r.communities ?? base.subscription.communities),
      channels,
      custom_feeds: uniqueStrings(sub.custom_feeds ?? r.custom_feeds ?? []),
      custom_feed_ids: uniqueInts(sub.custom_feed_ids ?? r.custom_feed_ids ?? []),
      keywords: uniqueStrings(sub.keywords ?? r.keywords ?? []).slice(0, 20),
      exclude_keywords: uniqueStrings(sub.exclude_keywords ?? r.exclude_keywords ?? []).slice(0, 20),
    },
    notifications: {
      daily_enabled: typeof (notif.daily_enabled ?? r.daily_enabled) === 'boolean'
        ? Boolean(notif.daily_enabled ?? r.daily_enabled)
        : true,
      daily_sources: uniqueStrings(notif.daily_sources ?? r.daily_sources ?? []),
      daily_time: TIME_PATTERN.test(String(notif.daily_time ?? r.daily_time ?? ''))
        ? String(notif.daily_time ?? r.daily_time)
        : '08:30',
      updates_enabled: typeof (notif.updates_enabled ?? r.updates_enabled) === 'boolean'
        ? Boolean(notif.updates_enabled ?? r.updates_enabled)
        : true,
      updates_sources: uniqueStrings(notif.updates_sources ?? r.updates_sources ?? []),
      calendar_enabled: typeof (notif.calendar_enabled ?? r.calendar_enabled) === 'boolean'
        ? Boolean(notif.calendar_enabled ?? r.calendar_enabled)
        : true,
      calendar_kinds: uniqueStrings(notif.calendar_kinds ?? r.calendar_kinds ?? base.notifications.calendar_kinds)
        .filter((k) => CALENDAR_KIND_IDS.has(k)),
      calendar_time: TIME_PATTERN.test(String(notif.calendar_time ?? r.calendar_time ?? ''))
        ? String(notif.calendar_time ?? r.calendar_time)
        : '20:00',
      quiet_start: clampInt(notif.quiet_start ?? r.quiet_start, 23, 0, 23),
      quiet_end: clampInt(notif.quiet_end ?? r.quiet_end, 8, 0, 23),
      quiet_critical: Boolean(notif.quiet_critical ?? r.quiet_critical ?? false),
      merge_minutes: clampInt(notif.merge_minutes ?? r.merge_minutes, 0, 0, 60),
      min_push_interval_minutes: clampInt(notif.min_push_interval_minutes ?? r.min_push_interval_minutes, 0, 0),
      daily_style: DAILY_STYLES.has(notif.daily_style ?? r.daily_style)
        ? (notif.daily_style ?? r.daily_style)
        : 'compact',
      max_images: clampInt(notif.max_images ?? r.max_images, 1, 1, 9),
    },
    presentation: {
      notification_style: NOTIFY_STYLES.has(pres.notification_style) ? pres.notification_style : 'persona',
      notification_format: NOTIFY_FORMATS.has(pres.notification_format) ? pres.notification_format : 'card',
      instructions: typeof pres.instructions === 'string' ? pres.instructions : '',
      notification_provider_id: typeof pres.notification_provider_id === 'string' ? pres.notification_provider_id.trim() : '',
      notification_template: typeof pres.notification_template === 'string' && pres.notification_template.trim()
        ? pres.notification_template
        : base.presentation.notification_template,
      notification_item_template: typeof pres.notification_item_template === 'string' && pres.notification_item_template.trim()
        ? pres.notification_item_template
        : base.presentation.notification_item_template,
      show_details_hint: Boolean(pres.show_details_hint),
    },
    interaction: {
      tools: uniqueStrings(inter.tools ?? r.tools ?? base.interaction.tools).filter((t) => TOOL_IDS.has(t)),
      chat_controls: Boolean(inter.chat_controls ?? r.chat_controls ?? false),
      manager_ids: uniqueStrings(inter.manager_ids ?? r.manager_ids ?? []).slice(0, 20),
      reminders_enabled: Boolean(inter.reminders_enabled ?? r.reminders_enabled ?? false),
      activities_enabled: Boolean(inter.activities_enabled ?? r.activities_enabled ?? false),
    },
  };
}

export function toPluginTarget(serverRow) {
  const row = serverRow && typeof serverRow === 'object' ? { ...serverRow } : {};
  if (row.communities == null) {
    const comm = row.community ?? 'all';
    row.communities = comm === 'all' ? ALL_COMMUNITIES.map((c) => c.id) : [String(comm)];
  }
  return normalizeStoredTarget(row);
}

export function toServerTarget(pluginTarget) {
  const t = normalizeStoredTarget(pluginTarget);
  return {
    name: t.name,
    umo: t.umo,
    enabled: t.enabled,
    delivery_mode: 'astrbot',
    scope_mode: t.subscription.scope_mode,
    inherit_preferences: t.subscription.inherit_preferences,
    inherit_keywords: t.subscription.inherit_keywords,
    communities: t.subscription.communities.length > 0
      ? [...t.subscription.communities]
      : [...DEFAULT_COMMUNITIES],
    channels: t.subscription.scope_mode === 'legacy' || t.subscription.scope_mode === 'custom'
      ? []
      : [...t.subscription.channels],
    custom_feeds: [...t.subscription.custom_feeds],
    custom_feed_ids: [...t.subscription.custom_feed_ids],
    keywords: [...t.subscription.keywords],
    exclude_keywords: [...t.subscription.exclude_keywords],
    daily_enabled: t.notifications.daily_enabled,
    daily_sources: [...t.notifications.daily_sources],
    daily_time: t.notifications.daily_time,
    updates_enabled: t.notifications.updates_enabled,
    updates_sources: [...t.notifications.updates_sources],
    calendar_enabled: t.notifications.calendar_enabled,
    calendar_kinds: [...t.notifications.calendar_kinds],
    calendar_time: t.notifications.calendar_time,
    quiet_start: t.notifications.quiet_start,
    quiet_end: t.notifications.quiet_end,
    quiet_critical: t.notifications.quiet_critical,
    merge_minutes: t.notifications.merge_minutes,
    min_push_interval_minutes: t.notifications.min_push_interval_minutes,
    daily_style: t.notifications.daily_style,
    max_images: t.notifications.max_images,
    tools: [...t.interaction.tools],
    chat_controls: t.interaction.chat_controls,
    manager_ids: [...t.interaction.manager_ids],
    reminders_enabled: t.interaction.reminders_enabled,
    activities_enabled: t.interaction.activities_enabled,
  };
}

export function applySubscription(targets, umo, response) {
  if (!Array.isArray(targets) || !response || typeof response !== 'object') return false;
  for (const target of targets) {
    if (target?.umo !== umo) continue;
    target.subscription = target.subscription || {};
    target.notifications = target.notifications || {};
    for (const field of ['communities', 'keywords', 'exclude_keywords', 'channels', 'scope_mode']) {
      if (field in response && response[field] !== undefined) {
        target.subscription[field] = structuredClone(response[field]);
      }
    }
    for (const field of ['daily_enabled', 'daily_time', 'updates_enabled']) {
      if (field in response && response[field] !== undefined) {
        target.notifications[field] = response[field];
      }
    }
    return true;
  }
  return false;
}

export function normalizeStoredConfig(raw) {
  const base = defaultConfig();
  const r = raw && typeof raw === 'object' ? raw : {};
  let baseUrl = typeof r.base_url === 'string' && r.base_url.trim() ? r.base_url.trim().replace(/\/+$/, '') : base.base_url;
  if (baseUrl === 'http://anisignal.xyz' || baseUrl === 'http://www.anisignal.xyz') {
    baseUrl = 'https://anisignal.xyz';
  }
  return {
    base_url: baseUrl,
    tools_key: typeof r.tools_key === 'string' ? r.tools_key.trim() : '',
    card_font_path: typeof r.card_font_path === 'string' ? r.card_font_path.trim() : '',
    configuration_imported: Boolean(r.configuration_imported),
    qqbot_app_id: typeof r.qqbot_app_id === 'string' ? r.qqbot_app_id.trim() : '',
    qqbot_app_secret: typeof r.qqbot_app_secret === 'string' ? r.qqbot_app_secret.trim() : '',
    qqbot_markdown: Boolean(r.qqbot_markdown),
    onebot_http_url: typeof r.onebot_http_url === 'string' ? r.onebot_http_url.trim().replace(/\/+$/, '') : '',
    onebot_ws_url: typeof r.onebot_ws_url === 'string' ? r.onebot_ws_url.trim() : '',
    onebot_access_token: typeof r.onebot_access_token === 'string' ? r.onebot_access_token.trim() : '',
    targets: Array.isArray(r.targets) ? r.targets.map(normalizeStoredTarget) : [],
  };
}

export function loadConfigFile(filePath, logger) {
  try {
    if (!existsSync(filePath)) return defaultConfig();
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    return normalizeStoredConfig(parsed);
  } catch (err) {
    logger?.warn?.(`[anisignal] 读取配置文件失败 (${filePath})，使用默认配置: ${err?.message ?? err}`);
    return defaultConfig();
  }
}

export function saveConfigFile(filePath, config) {
  const dir = dirname(filePath);
  mkdirSync(dir, { recursive: true });
  const tmpPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  const normalized = normalizeStoredConfig(config);
  try {
    writeFileSync(tmpPath, `${JSON.stringify(normalized, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    renameSync(tmpPath, filePath);
  } catch (err) {
    try {
      unlinkSync(tmpPath);
    } catch {
      /* ignore missing tmp */
    }
    throw err;
  }
  return normalized;
}

export function configurationRevision(config) {
  const snapshot = {
    base_url: config.base_url,
    card_font_path: config.card_font_path,
    qqbot_app_id: config.qqbot_app_id,
    qqbot_markdown: config.qqbot_markdown,
    onebot_http_url: config.onebot_http_url,
    onebot_ws_url: config.onebot_ws_url,
    targets: config.targets,
  };
  return createHash('sha256').update(JSON.stringify(snapshot)).digest('hex').slice(0, 16);
}

export function maskSecretTail(secret, minLen = 8) {
  const s = typeof secret === 'string' ? secret.trim() : '';
  return {
    set: Boolean(s),
    tail: s.length >= minLen ? s.slice(-4) : '',
  };
}

export function quotaSummary(identity) {
  const quota = identity?.quota;
  if (!quota) return '';
  return (
    `自建监控 ${quota.custom_feeds_used}/${quota.max_feeds}，采集间隔至少 ${quota.min_interval_minutes} 分钟；` +
    `Bot 接入 ${quota.bot_clients_used}/${quota.max_bot_clients}，启用会话 ${quota.bot_targets_used}/${quota.max_bot_targets}；` +
    `今日通知 ${quota.notifications_used}/${quota.daily_notifications} 次。` +
    '通知额度由邮件与 Bot 共用，失败的发送尝试也计入，北京时间零点重置。'
  );
}

/**
 * 校验并合并前端设置页提交的 payload。密钥留空表示保留原值，显式 clear_* 清空。
 */
export function normalizeViewSubmission(payload, currentConfig) {
  const errors = [];
  const raw = payload && typeof payload === 'object' ? payload : {};
  const next = structuredClone(normalizeStoredConfig(currentConfig));

  const baseUrl = typeof raw.base_url === 'string' ? raw.base_url.trim().replace(/\/+$/, '') : next.base_url;
  if (!/^https?:\/\/[^\s/]+/i.test(baseUrl)) {
    errors.push('服务地址应以 http:// 或 https:// 开头');
  } else {
    next.base_url = baseUrl === 'http://anisignal.xyz' || baseUrl === 'http://www.anisignal.xyz'
      ? 'https://anisignal.xyz'
      : baseUrl;
  }

  const applySecret = (field, clearFlag, label) => {
    if (raw[clearFlag] === true) {
      next[field] = '';
    } else if (raw[field] !== undefined) {
      if (typeof raw[field] !== 'string') {
        errors.push(`${label}应为文字`);
      } else if (raw[field].trim()) {
        next[field] = raw[field].trim();
      }
    }
  };

  applySecret('tools_key', 'clear_tools_key', '用户 Token');
  applySecret('qqbot_app_secret', 'clear_qqbot_secret', 'QQ Bot AppSecret');
  applySecret('onebot_access_token', 'clear_onebot_token', 'OneBot AccessToken');

  if (raw.card_font_path !== undefined) {
    if (typeof raw.card_font_path !== 'string') errors.push('字体路径应为文字');
    else next.card_font_path = raw.card_font_path.trim();
  }
  if (raw.qqbot_app_id !== undefined) {
    if (typeof raw.qqbot_app_id !== 'string') errors.push('QQ Bot AppID 应为文字');
    else next.qqbot_app_id = raw.qqbot_app_id.trim();
  }
  if (raw.qqbot_markdown !== undefined) {
    next.qqbot_markdown = Boolean(raw.qqbot_markdown);
  }
  if (raw.onebot_http_url !== undefined) {
    const v = typeof raw.onebot_http_url === 'string' ? raw.onebot_http_url.trim().replace(/\/+$/, '') : '';
    if (v && !/^https?:\/\/[^\s/]+/i.test(v)) errors.push('OneBot HTTP 地址应以 http:// 或 https:// 开头');
    else next.onebot_http_url = v;
  }
  if (raw.onebot_ws_url !== undefined) {
    const v = typeof raw.onebot_ws_url === 'string' ? raw.onebot_ws_url.trim() : '';
    if (v && !/^wss?:\/\/[^\s/]+/i.test(v)) errors.push('OneBot WS 地址应以 ws:// 或 wss:// 开头');
    else next.onebot_ws_url = v;
  }

  if (raw.targets !== undefined) {
    if (!Array.isArray(raw.targets)) {
      errors.push('会话列表格式无效');
    } else {
      const seen = new Set();
      const targets = [];
      raw.targets.forEach((item, idx) => {
        const rawSub = item?.subscription || {};
        const rawNotif = item?.notifications || {};
        const rawInter = item?.interaction || {};
        const label = typeof item?.name === 'string' && item.name.trim() ? `「${item.name.trim()}」` : `第 ${idx + 1} 个会话`;

        for (const [k, title] of [['daily_time', '早报时间'], ['calendar_time', '收尾提醒时间']]) {
          const val = rawNotif[k] ?? item?.[k];
          if (val !== undefined && !TIME_PATTERN.test(String(val))) {
            errors.push(`${label}的${title}应为 HH:mm`);
          }
        }
        for (const [k, title] of [['keywords', '关注关键词'], ['exclude_keywords', '排除关键词']]) {
          const words = Array.isArray(rawSub[k]) ? rawSub[k] : [];
          if (words.length > 20) errors.push(`${label}的${title}最多 20 个`);
          if (words.some((w) => typeof w === 'string' && w.trim().length > 64)) {
            errors.push(`${label}的${title}每个不超过 64 字`);
          }
        }
        if (Array.isArray(rawInter.manager_ids) && rawInter.manager_ids.length > 20) {
          errors.push(`${label}的管理用户最多 20 个`);
        }

        const t = normalizeStoredTarget(item);
        if (!t.name) errors.push(`${label}缺少名称`);
        else if (t.name.length > 100) errors.push(`${label}的名称超过 100 字`);
        if (!UMO_PATTERN.test(t.umo)) {
          errors.push(`${label}的会话标识应形如 平台:GroupMessage:群号 或 平台:FriendMessage:用户`);
        } else if (seen.has(t.umo)) {
          errors.push(`会话标识重复：${t.umo}`);
        } else {
          seen.add(t.umo);
        }
        targets.push(t);
      });
      next.targets = targets;
    }
  }

  return { config: next, errors };
}
