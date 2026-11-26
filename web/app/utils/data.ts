// 领域模型与展示元数据；平台字典统一由 lexicon.ts 导出
import { PLATFORM_META, type PlatformKey } from './lexicon.ts'

export type Platform = PlatformKey
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

export const platformMeta: Record<Platform, { label: string; short: string; badge: string }> = PLATFORM_META

export const regionMeta: Record<Region, string> = {
  cn: '国服',
  global: '国际服',
}

export interface RawItem {
  id: string
  platform: Platform
  contentType?: string
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
  revisionCount?: number
  hasSourceRevision?: boolean
  previewMode: 'full' | 'partial' | 'external_only'
}
