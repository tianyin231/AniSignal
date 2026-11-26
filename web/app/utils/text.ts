/** 文本展示工具 */

const HTML_ENTITIES: Record<string, string> = { amp: '&', quot: '"', apos: '\'', lt: '<', gt: '>', nbsp: ' ' }

/** 平台标题 / 摘要里偶有未解码的 HTML 实体（&quot; &amp;amp; 等），展示前还原；可能被重复转义，最多解两层。 */
export function decodeEntities(value: string): string {
  let out = value
  for (let i = 0; i < 2 && out.includes('&'); i++) {
    out = out.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, code: string) => {
      if (code[0] === '#') {
        const n = code[1]?.toLowerCase() === 'x' ? Number.parseInt(code.slice(2), 16) : Number(code.slice(1))
        return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : match
      }
      return HTML_ENTITIES[code.toLowerCase()] ?? match
    })
  }
  return out
}

const PLATFORM_STICKER_RE = /\[(?:cube|heybox|heygirl|doge|emoji)_[^\[\]\s]{1,24}\]/gi

/**
 * 清理小黑盒等平台的原始表情代码（如 `[cube_喜欢]`、`[heygirl_rua!]`）并还原 HTML 实体。
 * 若清理后为空，则回退原始文本避免空白标题。
 */
export function cleanPlatformTitle(value: string): string {
  const decoded = decodeEntities(value || '')
  const cleaned = decoded
    .replace(PLATFORM_STICKER_RE, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
  return cleaned || decoded.trim()
}

/** 清理帖文正文/摘要中的平台表情占位码，保留换行结构。 */
export function cleanPlatformText(value: string): string {
  const decoded = decodeEntities(value || '')
  return decoded
    .replace(PLATFORM_STICKER_RE, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

export type MarkdownBlock =
  | { type: 'heading'; level: 1 | 2 | 3; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; items: string[] }
  | { type: 'quote'; text: string }
  | { type: 'code'; lang: string; text: string }
  | { type: 'hr' }

function stripInlineMdLinks(line: string): string {
  return line
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .trim()
}

/**
 * 轻量纯文本 Markdown 分块解析（无 raw HTML，零 XSS 风险），用于站内 README 与 Release Notes 排版。
 */
export function parseMarkdownBlocks(raw: string): MarkdownBlock[] {
  const text = (raw || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const lines = text.split('\n')
  const blocks: MarkdownBlock[] = []
  let i = 0

  while (i < lines.length) {
    const rawLine = lines[i] ?? ''
    const trimmed = rawLine.trim()
    if (!trimmed) {
      i++
      continue
    }

    // 围栏代码块
    const fence = /^```([a-zA-Z0-9_+#.-]*)\s*$/.exec(trimmed)
    if (fence) {
      const lang = fence[1] || ''
      const buf: string[] = []
      i++
      while (i < lines.length && !(lines[i] ?? '').trim().startsWith('```')) {
        buf.push(lines[i] ?? '')
        i++
      }
      if (i < lines.length) i++
      const code = buf.join('\n').trimEnd()
      if (code) blocks.push({ type: 'code', lang, text: code })
      continue
    }

    // 分隔线
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push({ type: 'hr' })
      i++
      continue
    }

    // 标题
    const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed)
    if (heading) {
      const level = Math.min(3, heading[1]!.length) as 1 | 2 | 3
      const content = stripInlineMdLinks(heading[2]!.replace(/\s*#+\s*$/, ''))
      if (content) blocks.push({ type: 'heading', level, text: content })
      i++
      continue
    }

    // 引用块
    if (trimmed.startsWith('>')) {
      const qLines: string[] = []
      while (i < lines.length && (lines[i] ?? '').trim().startsWith('>')) {
        const piece = stripInlineMdLinks((lines[i] ?? '').trim().replace(/^>+\s?/, ''))
        if (piece) qLines.push(piece)
        i++
      }
      if (qLines.length) blocks.push({ type: 'quote', text: qLines.join('\n') })
      continue
    }

    // 无序 / 有序列表
    if (/^(?:[-*+]|\d+[.)])\s+/.test(trimmed)) {
      const items: string[] = []
      while (i < lines.length) {
        const cur = (lines[i] ?? '').trim()
        const m = /^(?:[-*+]|\d+[.)])\s+(.+)$/.exec(cur)
        if (!m) break
        const itemText = stripInlineMdLinks(m[1]!)
        if (itemText) items.push(itemText)
        i++
      }
      if (items.length) blocks.push({ type: 'list', items })
      continue
    }

    // 普通段落
    const pLines: string[] = []
    while (i < lines.length) {
      const cur = (lines[i] ?? '').trim()
      if (
        !cur
        || cur.startsWith('```')
        || cur.startsWith('#')
        || cur.startsWith('>')
        || /^(?:[-*+]|\d+[.)])\s+/.test(cur)
        || /^(?:-{3,}|\*{3,}|_{3,})$/.test(cur)
      ) {
        break
      }
      const cleaned = stripInlineMdLinks(cur)
      if (cleaned) pLines.push(cleaned)
      i++
    }
    if (pLines.length) blocks.push({ type: 'paragraph', text: pLines.join(' ') })
  }

  return blocks
}

function comparable(value: string): string {
  return value.replace(/\s+/g, '').replace(/(…|\.{3})$/, '').toLowerCase()
}

/**
 * 摘要是否只是在重复标题：X / 动态类内容的标题常取自正文开头，
 * 两者并排展示时同一句话会出现两遍。
 */
export function isRedundantExcerpt(title: string | undefined, excerpt: string | undefined): boolean {
  if (!title || !excerpt) return false
  const t = comparable(title)
  const e = comparable(excerpt)
  if (!t || !e) return false
  if (e === t || t.startsWith(e)) return true
  // 标题只是正文开头的几个字时，摘要仍有补充信息，保留
  return e.startsWith(t) && t.length >= Math.max(8, e.length * 0.5)
}
