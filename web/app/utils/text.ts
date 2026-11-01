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
