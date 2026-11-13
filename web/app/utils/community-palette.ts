/**
 * 分区调色板与动态主题样式：
 * - 兼容内置 8 种语义色键（blue/yellow/sky/violet/orange/cyan/lime/teal）
 * - 支持 16 色精选色板与任意 #RRGGBB 十六进制颜色，自动派生浅/深色模式可读字色与底色。
 */

export type NamedCommunityColor = 'blue' | 'yellow' | 'sky' | 'violet' | 'orange' | 'cyan' | 'lime' | 'teal'
export type CommunityColorValue = NamedCommunityColor | `#${string}` | string

export const NAMED_COLOR_HEX: Record<NamedCommunityColor, string> = {
  blue: '#2354f0',
  sky: '#1e93d8',
  cyan: '#4d9cb4',
  teal: '#12a08e',
  lime: '#a8b638',
  yellow: '#d4a21c',
  orange: '#ec741c',
  violet: '#7a5ae8',
}

export interface PaletteSwatch {
  value: string
  label: string
  hex: string
}

export const PALETTE_SWATCHES: readonly PaletteSwatch[] = [
  { value: 'blue', label: '品牌蓝', hex: NAMED_COLOR_HEX.blue },
  { value: 'sky', label: '天空蓝', hex: NAMED_COLOR_HEX.sky },
  { value: 'cyan', label: '青碧色', hex: NAMED_COLOR_HEX.cyan },
  { value: 'teal', label: '松石青', hex: NAMED_COLOR_HEX.teal },
  { value: '#10b981', label: '翠竹绿', hex: '#10b981' },
  { value: 'lime', label: '荧光绿', hex: NAMED_COLOR_HEX.lime },
  { value: 'yellow', label: '琥珀黄', hex: NAMED_COLOR_HEX.yellow },
  { value: 'orange', label: '活力橙', hex: NAMED_COLOR_HEX.orange },
  { value: '#ef4444', label: '珊瑚红', hex: '#ef4444' },
  { value: '#e11d48', label: '玫瑰红', hex: '#e11d48' },
  { value: '#ec4899', label: '樱花粉', hex: '#ec4899' },
  { value: '#a855f7', label: '梅子紫', hex: '#a855f7' },
  { value: 'violet', label: '星穹紫', hex: NAMED_COLOR_HEX.violet },
  { value: '#4f46e5', label: '靛青蓝', hex: '#4f46e5' },
  { value: '#a16207', label: '暖棕金', hex: '#a16207' },
  { value: '#475569', label: '岩板灰', hex: '#475569' },
]

const HEX6_RE = /^#([0-9a-f]{6})$/i

/** 解析 #RRGGBB 为小写 6 位十六进制（不含 #）；非十六进制色返回 null。 */
export function parseHex6(color?: string | null): string | null {
  if (!color) return null
  const m = HEX6_RE.exec(color.trim())
  return m ? m[1]!.toLowerCase() : null
}

/** 任意分区颜色键转 #rrggbb（供色块预览与原生 color input 绑定）。 */
export function colorToHex(color?: string | null): string {
  const hex6 = parseHex6(color)
  if (hex6) return `#${hex6}`
  if (color && color in NAMED_COLOR_HEX) return NAMED_COLOR_HEX[color as NamedCommunityColor]
  return NAMED_COLOR_HEX.blue
}

export function hexCommunityClasses(hex6: string) {
  return {
    chip: `c-chip-${hex6}`,
    nav: `c-nav-${hex6}`,
    dot: `c-dot-${hex6}`,
    text: `c-text-${hex6}`,
  }
}

export function hexChannelThemeClass(hex6: string): string {
  return `ch-hex-${hex6}`
}

function mixRgb(rgb: [number, number, number], target: [number, number, number], ratio: number): string {
  const clamp = (n: number) => Math.max(0, Math.min(255, Math.round(n)))
  const r = clamp(rgb[0] * (1 - ratio) + target[0] * ratio)
  const g = clamp(rgb[1] * (1 - ratio) + target[1] * ratio)
  const b = clamp(rgb[2] * (1 - ratio) + target[2] * ratio)
  return `#${[r, g, b].map(v => v.toString(16).padStart(2, '0')).join('')}`
}

/** 为所有当前用到的 #RRGGBB 分区颜色生成 SSR/CSR 一致的浅深色主题规则。 */
export function buildDynamicColorCss(colors: Iterable<string | null | undefined>): string {
  const seen = new Set<string>()
  const rules: string[] = []
  for (const raw of colors) {
    const h = parseHex6(raw)
    if (!h || seen.has(h)) continue
    seen.add(h)
    const r = Number.parseInt(h.slice(0, 2), 16)
    const g = Number.parseInt(h.slice(2, 4), 16)
    const b = Number.parseInt(h.slice(4, 6), 16)
    const base = `#${h}`
    const lightInk = mixRgb([r, g, b], [12, 18, 28], 0.34)
    const darkBase = mixRgb([r, g, b], [255, 255, 255], 0.18)
    const darkInk = mixRgb([r, g, b], [255, 255, 255], 0.42)
    const lightSoft = `rgb(${r} ${g} ${b} / 0.12)`
    const darkSoft = `rgb(${r} ${g} ${b} / 0.16)`
    rules.push(
      `.ch-hex-${h}{--sd-accent:${base};--sd-accent-ink:${lightInk};--sd-accent-soft:${lightSoft}}`,
      `.dark .ch-hex-${h},.ch-hex-${h}.dark{--sd-accent:${darkBase};--sd-accent-ink:${darkInk};--sd-accent-soft:${darkSoft}}`,
      `.c-dot-${h}{background-color:${base}}.dark .c-dot-${h}{background-color:${darkBase}}`,
      `.c-nav-${h}{border-color:${base};color:var(--sd-ink)}.dark .c-nav-${h}{border-color:${darkBase}}`,
      `.c-text-${h}{color:${lightInk}}.dark .c-text-${h}{color:${darkInk}}`,
      `.c-chip-${h}{background-color:${lightSoft};color:${lightInk}}.dark .c-chip-${h}{background-color:${darkSoft};color:${darkInk}}`,
    )
  }
  return rules.join('\n')
}
