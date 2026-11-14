/**
 * 轻量级情报笺 SVG 构建与可选 @resvg/resvg-js PNG 栅格化。
 * 未安装 @resvg/resvg-js 或缺少中文字体时返回 null，调用方自动降级为纯文本早报。
 */
import { closeSync, existsSync, openSync, readSync } from 'node:fs';

const HANGING_PUNCT = new Set([...'，。！？；：、）》】”’!?.,;:)']);

const COMMUNITY_COLORS = {
  wuwa: '#bc922f',
  blhx: '#368fba',
  hsr: '#8671b8',
  arknights: '#c7803c',
  genshin: '#3b82a6',
  endfield: '#4d6b57',
  zzz: '#d4652f',
  ai: '#387861',
};

const FALLBACK_LABELS = {
  wuwa: '鸣潮',
  blhx: '碧蓝航线',
  hsr: '星穹铁道',
  arknights: '明日方舟',
  genshin: '原神',
  endfield: '终末地',
  zzz: '绝区零',
  ai: 'AI',
};

const FONT_CANDIDATES = [
  '/System/Library/Fonts/PingFang.ttc',
  '/System/Library/Fonts/STHeiti Medium.ttc',
  '/System/Library/Fonts/Supplemental/Songti.ttc',
  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
  '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
  '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
  '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
  '/AstrBot/data/plugin_data/astrbot_plugin_anisignal/NotoSansCJKsc-Regular.otf',
  'C:/Windows/Fonts/msyh.ttc',
  'C:/Windows/Fonts/simhei.ttf',
];

const SVG_FONT_STACK = [
  'PingFang SC',
  'Heiti SC',
  'Songti SC',
  'Noto Sans CJK SC',
  'Noto Sans SC',
  'Source Han Sans SC',
  'WenQuanYi Zen Hei',
  'Microsoft YaHei',
  'SimHei',
  'HarmonyOS Sans SC',
  'sans-serif',
].map((name) => (name.includes(' ') ? `'${name}'` : name)).join(', ');

let cachedResvgModule;
const fontFamilyCache = new Map();

function detectFontFamily(filePath) {
  if (fontFamilyCache.has(filePath)) return fontFamilyCache.get(filePath);
  let fd;
  let detected = '';
  try {
    fd = openSync(filePath, 'r');
    const head = Buffer.alloc(65536);
    const bytes = readSync(fd, head, 0, head.length, 0);
    const offsets = [];
    if (bytes >= 16 && head.toString('ascii', 0, 4) === 'ttcf') {
      const count = Math.min(head.readUInt32BE(8), 12);
      for (let idx = 0; idx < count && 12 + (idx + 1) * 4 <= bytes; idx += 1) {
        offsets.push(head.readUInt32BE(12 + idx * 4));
      }
    } else if (bytes >= 12) {
      offsets.push(0);
    }

    for (const sfnt of offsets) {
      if (sfnt + 12 > bytes) continue;
      const numTables = head.readUInt16BE(sfnt + 4);
      let nameOff = 0;
      let nameLen = 0;
      for (let i = 0; i < numTables && sfnt + 12 + (i + 1) * 16 <= bytes; i += 1) {
        const rec = sfnt + 12 + i * 16;
        if (head.toString('ascii', rec, rec + 4) === 'name') {
          nameOff = head.readUInt32BE(rec + 8);
          nameLen = head.readUInt32BE(rec + 12);
          break;
        }
      }
      if (!nameOff || !nameLen) continue;
      const tbl = Buffer.alloc(Math.min(nameLen, 65536));
      const got = readSync(fd, tbl, 0, tbl.length, nameOff);
      if (got < 6) continue;
      const count = tbl.readUInt16BE(2);
      const storage = tbl.readUInt16BE(4);
      for (const targetId of [16, 1]) {
        for (let i = 0; i < count && 6 + (i + 1) * 12 <= got; i += 1) {
          const r = 6 + i * 12;
          const pid = tbl.readUInt16BE(r);
          const nid = tbl.readUInt16BE(r + 6);
          const len = tbl.readUInt16BE(r + 8);
          const off = storage + tbl.readUInt16BE(r + 10);
          if (nid !== targetId || len === 0 || off + len > got) continue;
          let family = '';
          if (pid === 3 || pid === 0) {
            const u16 = Buffer.alloc(len);
            for (let j = 0; j + 1 < len; j += 2) {
              u16[j] = tbl[off + j + 1];
              u16[j + 1] = tbl[off + j];
            }
            family = u16.toString('utf16le').replace(/\0/g, '').trim();
          } else if (pid === 1) {
            family = tbl.toString('ascii', off, off + len).replace(/\0/g, '').trim();
          }
          if (family && !family.startsWith('.')) {
            detected = family;
            break;
          }
        }
        if (detected) break;
      }
      if (detected) break;
    }
  } catch {
    detected = '';
  } finally {
    if (fd !== undefined) {
      try {
        closeSync(fd);
      } catch {
        /* ignore */
      }
    }
  }
  fontFamilyCache.set(filePath, detected);
  return detected;
}

export function escapeXml(value) {
  return String(value ?? '')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function estimateCharWidth(ch, fontSize) {
  const code = ch.codePointAt(0) ?? 0;
  if (code <= 0x7f) return fontSize * 0.56;
  if (code >= 0xff61 && code <= 0xff9f) return fontSize * 0.56;
  return fontSize;
}

// shortcut: 按全角/半角字宽系数折行而非逐字体读取 glyph 表, 如需像素级对齐可引入 opentype.js
export function wrapCardLines(rawText, maxWidth, fontSize, maxLines = 2) {
  const clean = String(rawText ?? '').replace(/\s+/g, ' ').trim();
  if (!clean) return [];
  const lines = [];
  let current = '';
  let currentWidth = 0;

  for (const ch of clean) {
    if (HANGING_PUNCT.has(ch) && current) {
      current += ch;
      currentWidth += estimateCharWidth(ch, fontSize);
      continue;
    }
    const w = estimateCharWidth(ch, fontSize);
    if (current && currentWidth + w > maxWidth) {
      lines.push(current);
      current = ch;
      currentWidth = w;
    } else {
      current += ch;
      currentWidth += w;
    }
  }
  if (current) lines.push(current);

  if (lines.length > maxLines) {
    const sliced = lines.slice(0, maxLines);
    const chars = [...sliced[maxLines - 1]];
    if (chars.length > 0) chars[chars.length - 1] = '…';
    else chars.push('…');
    sliced[maxLines - 1] = chars.join('');
    return sliced;
  }
  return lines;
}

export function resolveCardFont(customPath = '') {
  const list = customPath ? [customPath, ...FONT_CANDIDATES] : FONT_CANDIDATES;
  for (const candidate of list) {
    if (candidate && existsSync(candidate)) return candidate;
  }
  return null;
}

export function buildBriefCardSvg(brief = {}, { detailHint = '' } = {}) {
  const items = Array.isArray(brief?.items) ? brief.items.slice(0, 3) : [];
  const width = 960;
  const height = 180 + items.length * 166 + 72;
  const ink = '#203c35';
  const green = '#387861';
  const muted = '#63766f';
  const datePart = String(brief?.date || '').split(' ')[0] || '';
  const subtitle = `${brief?.title || '今日速报'}  /  ${datePart ? `${datePart} · ` : ''}北京时间`;

  const blocks = [];
  items.forEach((item, idx) => {
    const y = 163 + idx * 166;
    const community = String(item?.community || '');
    const color = COMMUNITY_COLORS[community] || green;
    const label = item?.community_label || FALLBACK_LABELS[community] || '情报';
    const pubLabel = item?.published_label ? String(item.published_label) : '';
    const titleLines = wrapCardLines(item?.title || '新动态', 760, 30, 1);
    const summaryLines = wrapCardLines(item?.summary || '', 760, 25, 2);

    blocks.push(
      `<line x1="52" y1="${y - 8}" x2="908" y2="${y - 8}" stroke="#e7ebe3" stroke-width="2" />`,
      `<text x="52" y="${y + 44}" font-size="34" font-weight="600" fill="${color}">0${idx + 1}</text>`,
      `<text x="127" y="${y + 25}" font-size="20" font-weight="600" fill="${color}">${escapeXml(label)}</text>`,
    );
    if (pubLabel) {
      blocks.push(
        `<text x="907" y="${y + 24}" font-size="18" fill="${muted}" text-anchor="end">${escapeXml(pubLabel)}</text>`,
      );
    }
    titleLines.forEach((line, n) => {
      blocks.push(
        `<text x="126" y="${y + 69 + n * 36}" font-size="30" font-weight="600" fill="${ink}">${escapeXml(line)}</text>`,
      );
    });
    summaryLines.forEach((line, n) => {
      blocks.push(
        `<text x="127" y="${y + 111 + n * 32}" font-size="25" fill="${muted}">${escapeXml(line)}</text>`,
      );
    });
  });

  if (detailHint) {
    blocks.push(
      `<text x="54" y="${height - 43}" font-size="23" fill="${green}">${escapeXml(detailHint)}</text>`,
    );
  }
  blocks.push(
    `<text x="906" y="${height - 43}" font-size="19" fill="${muted}" text-anchor="end">AniSignal</text>`,
  );

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="${SVG_FONT_STACK}">`,
    `<rect x="0" y="0" width="${width}" height="${height}" fill="#f6f4ed" />`,
    `<rect x="20" y="20" width="${width - 40}" height="${height - 40}" rx="24" ry="24" fill="#fffefa" />`,
    `<rect x="48" y="47" width="7" height="71" rx="3" ry="3" fill="${green}" />`,
    `<text x="76" y="76" font-size="38" font-weight="700" fill="${ink}">AniSignal 情报笺</text>`,
    `<text x="78" y="121" font-size="23" fill="${muted}">${escapeXml(subtitle)}</text>`,
    ...blocks,
    '</svg>',
  ].join('\n');
}

async function loadResvg(customLoader) {
  if (customLoader) return customLoader();
  if (cachedResvgModule !== undefined) return cachedResvgModule;
  try {
    cachedResvgModule = await import('@resvg/resvg-js');
  } catch {
    cachedResvgModule = null;
  }
  return cachedResvgModule;
}

export async function inspectCardCapability(customFontPath = '', customLoader = null) {
  const fontFile = resolveCardFont(customFontPath);
  const mod = await loadResvg(customLoader);
  const Resvg = mod?.Resvg;
  if (!Resvg) {
    return {
      available: false,
      resvgLoaded: false,
      fontFile,
      reason: '未安装可选依赖 @resvg/resvg-js（早报将自动以纯文本发送）',
    };
  }
  if (!fontFile) {
    return {
      available: false,
      resvgLoaded: true,
      fontFile: null,
      reason: '未找到可用中文字体文件，请配置字体路径或安装 Noto Sans CJK',
    };
  }
  return {
    available: true,
    resvgLoaded: true,
    fontFile,
    reason: '',
  };
}

export async function renderBriefCardPng(brief, { fontPath = '', detailHint = '', resvgLoader = null } = {}) {
  const fontFile = resolveCardFont(fontPath);
  if (!fontFile) return null;
  const mod = await loadResvg(resvgLoader);
  const Resvg = mod?.Resvg;
  if (!Resvg) return null;

  const family = detectFontFamily(fontFile);
  const svg = buildBriefCardSvg(brief, { detailHint });
  const resvg = new Resvg(svg, {
    font: {
      fontFiles: [fontFile],
      loadSystemFonts: false,
      ...(family ? { sansSerifFamily: family, defaultFontFamily: family } : {}),
    },
  });
  const pngData = resvg.render().asPng();
  return Buffer.from(pngData);
}
