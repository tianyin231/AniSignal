// 领域模型与演示数据，对应设计文档 §7；全部为 mock，时间基准 2026-08-16

export type Platform = 'bilibili' | 'weibo' | 'x' | 'youtube' | 'web' | 'kuro' | 'miyoushe' | 'kuro_cn' | 'kuro_global' | 'rss' | 'game_official'
export type Identity = 'official' | 'seeded' | 'user' | 'media' | 'merchant' | 'unknown'
export type Region = 'cn' | 'global'

export interface SourceRef {
  platform: Platform
  account: string
  region: Region
  publishedAt: string
  foundAt: string
  translated?: boolean
  contentId?: number | null
  url: string
}

export interface AppEvent {
  id: string
  title: string
  summary: string
  category: string
  identity: Identity
  regions: Region[]
  sources: SourceRef[]
  evidenceCount: number
  firstSeenAt: string
  latestEvidenceAt: string
  updatedAt: string
  important?: boolean
  confidence: '高' | '中' | '低'
  conclusion: string
  statusChange?: string
  facts?: { label: string; value: string }[]
  community?: string
  matchedKeywords?: string[]
  regionNote?: string
  conflicts?: string
  corrections?: { at: string; note: string }[]
}

export interface ProductStage {
  label: string
  state: 'done' | 'current' | 'pending'
  tone?: 'amber' | 'sky' | 'emerald'
  date?: string
  evidence?: number
  note?: string
}

export interface Product {
  slug: string
  name: string
  publisher: string
  skus: string[]
  price: string
  presaleAt: string
  balanceFrom: string
  balanceTo: string
  originalShipAt: string
  currentShipNote: string
  currentStage: number
  stages: ProductStage[]
}

export const identityMeta: Record<Identity, { label: string; badge: string; text: string }> = {
  official: {
    label: '官方',
    badge: 'bg-blue-500/10 text-blue-600 dark:bg-blue-400/15 dark:text-blue-300',
    text: 'text-blue-700 dark:text-blue-300',
  },
  seeded: {
    label: '官方送测',
    badge: 'bg-amber-500/10 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300',
    text: 'text-amber-700 dark:text-amber-300',
  },
  user: {
    label: '普通用户',
    badge: 'bg-emerald-500/10 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  media: {
    label: '媒体',
    badge: 'bg-sky-500/10 text-sky-700 dark:bg-sky-400/15 dark:text-sky-300',
    text: 'text-sky-700 dark:text-sky-300',
  },
  merchant: {
    label: '商家',
    badge: 'bg-teal-600/10 text-teal-700 dark:bg-teal-400/15 dark:text-teal-300',
    text: 'text-teal-700 dark:text-teal-300',
  },
  unknown: {
    label: '来源未说明',
    badge: 'bg-zinc-500/10 text-zinc-600 dark:bg-zinc-400/15 dark:text-zinc-300',
    text: 'text-zinc-500 dark:text-zinc-400',
  },
}

export const platformMeta: Record<Platform, { label: string; short: string; badge: string }> = {
  bilibili: { label: 'B站', short: 'B', badge: 'bg-pink-500' },
  weibo: { label: '微博', short: '博', badge: 'bg-red-600' },
  x: { label: 'X', short: 'X', badge: 'bg-zinc-900 dark:bg-zinc-700' },
  rss: { label: '新闻源', short: 'RSS', badge: 'bg-teal-700' },
  youtube: { label: 'YouTube', short: 'YT', badge: 'bg-red-500' },
  game_official: { label: '游戏官网', short: '官', badge: 'bg-emerald-600' },
  web: { label: '官网', short: '网', badge: 'bg-sky-600' },
  kuro: { label: '库街区', short: '库', badge: 'bg-teal-600' },
  miyoushe: { label: '米游社', short: '米', badge: 'bg-sky-500' },
  kuro_cn: { label: '库街区·国服', short: '库', badge: 'bg-sky-600' },
  kuro_global: { label: '库街区·国际服', short: '际', badge: 'bg-cyan-600' },
}

export const regionMeta: Record<Region, string> = {
  cn: '国服',
  global: '国际服',
}

export interface RawItem {
  id: string
  platform: Platform
  account: string
  region: Region
  title: string
  publishedAt: string
  identity: Identity
  category?: string
  translated?: boolean
  mergedInto?: string
  keywords?: string[]
  excerpt?: string
  cover?: string
  originalUrl?: string
  metrics?: Record<string, number>
  community?: string
  previewMode: 'full' | 'partial' | 'external_only'
}
