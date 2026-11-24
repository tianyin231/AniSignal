/**
 * 通知快照渲染与人格短开场清洗。
 * 与 integrations/astrbot_plugin_anisignal/notification_template.py 对齐，只做单遍安全的占位符替换。
 */

const TOKEN_RE = /\{([a-z_]+)\}/g;
const FIELDS = new Set([
  'title', 'summary', 'content', 'original', 'time', 'source', 'url',
  'images', 'image_urls', 'items', 'index', 'schedule', 'details_hint',
]);
export const DEFAULT_ITEM_TEMPLATE = '{index}. {title}\n{summary}\n发布时间：{time}\n来源：{source}\n{url}\n{images}';
const KINDS = {
  daily: '每日早报',
  updates: '重要动态',
  critical: '关键动态',
  revisions: '官方修订',
  calendar: '活动提醒',
  reminder: '定时提醒',
  manual: '情报通知',
};

export const INTRO_LIMIT = 60;
const FIRST_SENTENCE_RE = /^.+?(?:[。！？!?~～…]+|\.(?=\s|$))[）)」』"”'’]*/;

function unique(values) {
  const seen = new Set();
  const out = [];
  for (const v of values) {
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

export function formatBeijingTime(value) {
  if (!value) return '';
  const str = String(value).trim();
  // Python naive ISO string -> treat as UTC
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(str) ? str : `${str}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return '';
  const bj = new Date(d.getTime() + 8 * 3600_000);
  const p = (n) => String(n).padStart(2, '0');
  return `${bj.getUTCFullYear()}-${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}`;
}

export function currentBeijingLabel(now = new Date()) {
  const bj = new Date(now.getTime() + 8 * 3600_000);
  const p = (n) => String(n).padStart(2, '0');
  const week = ['日', '一', '二', '三', '四', '五', '六'][bj.getUTCDay()];
  return {
    slashTime: `${bj.getUTCFullYear()}/${p(bj.getUTCMonth() + 1)}/${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}:${p(bj.getUTCSeconds())}`,
    shortMinute: `${bj.getUTCFullYear()}/${p(bj.getUTCMonth() + 1)}/${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}`,
    contextLine: `当前北京时间：${bj.getUTCFullYear()}-${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}，星期${week}`,
  };
}

export function detailsHint(brief, enabled = false) {
  const items = brief?.items || [];
  if (!enabled || items.length === 0) return '';
  let omitted = false;
  if (brief?.mode === 'calendar') {
    omitted = Boolean(brief?.truncated);
  } else {
    omitted = items.some((item) => {
      const sources = item?.sources || [];
      if (sources.length > 1) return true;
      const known = new Set([item?.title || '', item?.summary || '', item?.share_text || '']);
      return sources.some((s) => {
        const text = String(s?.text || '').trim();
        return text && !known.has(text);
      });
    });
  }
  if (!omitted) return '';
  const num = Math.min(2, items.length);
  return `查看详情：可说「展开第 ${num} 条」${items.length > 1 ? '（数字可换）' : ''}`;
}

function formatSchedule(entries) {
  if (!Array.isArray(entries)) return '';
  return entries
    .map((entry) => {
      const action = entry?.kind === 'maintenance' ? '维护开始' : '截止';
      const base = `${entry?.title || ''}\n${action}：${formatBeijingTime(entry?.stage_at)}`;
      return entry?.url ? `${base}\n${entry.url}` : base;
    })
    .join('\n\n');
}

function buildItemFields(item, index, allowedImages) {
  const sources = Array.isArray(item?.sources) && item.sources.length > 0 ? item.sources : [item];
  const content = unique(sources.map((s) => s?.text || '')).join('\n\n');
  const ownImages = unique([item?.image, ...sources.map((s) => s?.image)]);
  const allowedSet = new Set(allowedImages);
  const images = ownImages.filter((u) => allowedSet.has(u));
  return {
    index: String(index),
    title: String(item?.title || ''),
    summary: String(item?.summary || item?.share_text || ''),
    content,
    original: content,
    time: unique(sources.map((s) => formatBeijingTime(s?.published_at))).join(' / '),
    source: unique(sources.map((s) => s?.source || s?.platform || '')).join(' / '),
    url: unique(sources.map((s) => s?.url || '')).join('\n'),
    images,
    image_urls: images.join('\n'),
    schedule: formatSchedule(item?.calendar_entries || []),
  };
}

export function notificationPlaintext(raw) {
  let text = String(raw ?? '')
    .replace(/&(?:amp|#0*38|#x0*26);/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'");
  text = text.replace(/<[^>]*>|&&[A-Za-z0-9_]{1,64}&&|\[\[[^\]]*\]\]/g, '');
  return text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '');
}

export function introWeightedLength(text) {
  let len = 0;
  for (const ch of String(text || '')) {
    const code = ch.codePointAt(0) ?? 0;
    len += code >= 0x2e80 ? 1 : 0.5;
  }
  return len;
}

export function sanitizeShortIntro(raw, limit = INTRO_LIMIT) {
  const cleaned = notificationPlaintext(raw).replace(/\s+/g, ' ').trim();
  if (!cleaned || cleaned === '[NO_REPLY]' || cleaned === '[SILENT]') return '';
  if (limit === null || introWeightedLength(cleaned) <= limit) return cleaned;
  const match = cleaned.match(FIRST_SENTENCE_RE);
  if (match && introWeightedLength(match[0]) <= limit) {
    return match[0].trim();
  }
  return '';
}

/**
 * 渲染通知为文本与图片组件序列。
 * @returns {{ parts: Array<{type: 'text', text: string} | {type: 'image', url: string}>, customized: boolean, text: string }}
 */
export function renderNotification(row, options = {}, intro = '') {
  let template = options.notification_template || '{intro}\n\n{body}';
  let customized = !template.includes('{body}');
  if (!customized) {
    for (const m of template.matchAll(TOKEN_RE)) {
      if (FIELDS.has(m[1])) {
        customized = true;
        break;
      }
    }
  }

  const maxImages = Math.max(1, Math.min(9, Number(row?.max_images ?? 1) || 1));
  const images = unique(Array.isArray(row?.images) ? row.images : []).slice(0, maxImages);
  const brief = row?.brief || {};
  const hint = detailsHint(brief, Boolean(options.show_details_hint));

  if (!customized && hint && !(brief.mode === 'daily' && (options.notification_format ?? 'card') === 'card')) {
    template += '\n\n{details_hint}';
  }

  const rawItems = Array.isArray(brief.items) && brief.items.length > 0
    ? brief.items
    : [{ title: brief.title || KINDS[row?.kind] || '情报通知', text: String(row?.body || '') }];
  const fields = rawItems.map((it, idx) => buildItemFields(it, idx + 1, images));
  if (fields.length === 1 && fields[0].images.length === 0) {
    fields[0].images = images;
    fields[0].image_urls = images.join('\n');
  }

  const values = {
    intro: String(intro || ''),
    body: String(row?.body || ''),
    images,
    image_urls: images.join('\n'),
    details_hint: hint,
  };
  for (const name of FIELDS) {
    if (name === 'items' || name === 'images' || name === 'image_urls' || name === 'details_hint') continue;
    values[name] = fields
      .map((item, idx) => (item[name] ? `${fields.length > 1 && name !== 'index' ? `${idx + 1}. ` : ''}${item[name]}` : ''))
      .filter(Boolean)
      .join('\n\n');
  }

  const parts = [];
  const emittedImages = new Set();
  const appendText = (str) => {
    if (!str) return;
    const prev = parts[parts.length - 1];
    if (prev && prev.type === 'text') prev.text += str;
    else parts.push({ type: 'text', text: str });
  };
  const appendImage = (url) => {
    if (!url || emittedImages.has(url)) return;
    emittedImages.add(url);
    parts.push({ type: 'image', url });
  };

  const renderPass = (tpl, data, allowItems = false) => {
    let pos = 0;
    for (const match of tpl.matchAll(TOKEN_RE)) {
      appendText(tpl.slice(pos, match.index));
      const key = match[1];
      if (key === 'images') {
        for (const url of data.images || []) appendImage(url);
      } else if (key === 'items' && allowItems) {
        const itemTpl = options.notification_item_template || DEFAULT_ITEM_TEMPLATE;
        fields.forEach((item, idx) => {
          if (idx > 0) appendText('\n\n');
          renderPass(itemTpl, item, false);
        });
      } else if (Object.prototype.hasOwnProperty.call(data, key)) {
        appendText(String(data[key] ?? ''));
      } else {
        appendText(match[0]);
      }
      pos = (match.index ?? 0) + match[0].length;
    }
    appendText(tpl.slice(pos));
  };

  renderPass(template, values, true);

  const hasContent = parts.some((p) => p.type === 'image' || p.text.trim() !== '');
  const finalParts = hasContent ? parts : [{ type: 'text', text: String(row?.body || '') }];
  const fullText = finalParts
    .map((p) => (p.type === 'image' ? `[图片] ${p.url}` : p.text))
    .join('')
    .trim();

  return { parts: finalParts, customized, text: fullText };
}
