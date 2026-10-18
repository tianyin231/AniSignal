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
