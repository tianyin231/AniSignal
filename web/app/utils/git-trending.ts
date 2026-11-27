/** GitHub Trending 刊物展示与标记辅助函数（纯函数，含单测）。 */

import type { RawItem } from './data.ts'

export type TrendingPeriod = 'daily' | 'weekly' | 'monthly'

export interface TrendingPeriodMeta {
  period: TrendingPeriod
  label: string
  edition_count: number
  latest_key: string | null
  latest_captured_at: string | null
}

export interface TrendingEditionSummary {
  period_key: string
  issue_number: number
  item_count: number
  new_count: number
  captured_at: string
}

export interface ContentBrief {
  kind: 'repo' | 'release' | 'feed' | string
  headline: string
  tagline: string
  summary: string
  highlights: string[]
  use_cases: string[]
  tags: string[]
  model?: string | null
  updated_at?: string | null
}

export interface TrendingEntry {
  rank: number
  repo: string
  url: string
  description: string
  language: string
  stars: number
  forks: number
  period_stars: number
  raw_content_id: number | null
  status: 'new' | 'streak' | 'returning'
  streak_count: number
  appearances: number
  first_seen_key: string
  best_rank: number
  rank_delta: number | null
  also_in: TrendingPeriod[]
  brief?: ContentBrief | null
}

export interface TrendingEditionDetail {
  period: TrendingPeriod
  period_label: string
  period_key: string
  issue_number: number
  captured_at: string
  prev_key: string | null
  next_key: string | null
  stats: {
    total: number
    new_count: number
    streak_count: number
    returning_count: number
  }
  languages: { name: string; count: number }[]
  entries: TrendingEntry[]
}

export interface TrendingReleaseBrief {
  id: number
  title: string
  url: string | null
  published_at: string | null
  tag_name: string | null
  repo: string | null
  prerelease: boolean
  brief?: ContentBrief | null
}

export interface TrendingEditionsResponse {
  community: string
  has_editions: boolean
  active_period: TrendingPeriod
  periods: TrendingPeriodMeta[]
  editions: TrendingEditionSummary[]
  current: TrendingEditionDetail | null
  releases: TrendingReleaseBrief[]
}

const PERIOD_UNIT: Record<TrendingPeriod, string> = {
  daily: '天',
  weekly: '周',
  monthly: '月',
}

const PERIOD_GAIN_LABEL: Record<TrendingPeriod, string> = {
  daily: '今日新增',
  weekly: '本周新增',
  monthly: '本月新增',
}

const PERIOD_SHORT: Record<TrendingPeriod, string> = {
  daily: '日刊',
  weekly: '周刊',
  monthly: '月刊',
}

export function periodGainLabel(period: TrendingPeriod): string {
  return PERIOD_GAIN_LABEL[period] ?? '本期新增'
}

export function periodShortLabel(period: TrendingPeriod): string {
  return PERIOD_SHORT[period] ?? period
}

export function formatPeriodKey(period: TrendingPeriod, key: string): string {
  if (!key) return ''
  if (period === 'daily') {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key)
    if (m) return `${m[1]}/${m[2]}/${m[3]}`
  }
  if (period === 'weekly') {
    const m = /^(\d{4})-W(\d{2})$/.exec(key)
    if (m) return `${m[1]} 第 ${Number(m[2])} 周`
  }
  if (period === 'monthly') {
    const m = /^(\d{4})-(\d{2})$/.exec(key)
    if (m) return `${m[1]} 年 ${Number(m[2])} 月`
  }
  return key
}

export function formatCompactStars(n: number): string {
  const num = Number(n) || 0
  if (num >= 100_000) return `${(num / 1000).toFixed(0)}k`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}k`
  return num.toLocaleString('zh-CN')
}

export function splitRepoName(repo: string): { owner: string; name: string } {
  const idx = repo.indexOf('/')
  if (idx < 0) return { owner: '', name: repo }
  return { owner: repo.slice(0, idx), name: repo.slice(idx + 1) }
}

export interface TrendingStatusBadge {
  label: string
  tone: 'new' | 'streak' | 'returning'
}

export function trendingStatusBadge(
  entry: Pick<TrendingEntry, 'status' | 'streak_count' | 'appearances'>,
  period: TrendingPeriod,
): TrendingStatusBadge {
  const unit = PERIOD_UNIT[period] ?? '期'
  if (entry.status === 'new') {
    return { label: '首次上榜', tone: 'new' }
  }
  if (entry.status === 'streak') {
    return { label: `连榜 ${Math.max(2, entry.streak_count)} ${unit}`, tone: 'streak' }
  }
  return { label: `复榜 · 第 ${Math.max(2, entry.appearances)} 次`, tone: 'returning' }
}

const LANGUAGE_COLORS: Record<string, string> = {
  python: '#3572A5',
  typescript: '#3178C6',
  javascript: '#E6C229',
  rust: '#DEA584',
  go: '#00ADD8',
  'c++': '#F34B7D',
  c: '#6E7781',
  java: '#B07219',
  vue: '#41B883',
  swift: '#F05138',
  kotlin: '#A97BFF',
  shell: '#59A14F',
  css: '#663399',
  html: '#E34C26',
  'jupyter notebook': '#DA5B0B',
  'c#': '#178600',
  ruby: '#CC342D',
  php: '#4F5D95',
  zig: '#EC915C',
  dart: '#00B4AB',
  lua: '#000080',
  scala: '#DC322F',
  elixir: '#6E4A7E',
}

export function languageColor(language: string): string {
  return LANGUAGE_COLORS[(language || '').trim().toLowerCase()] ?? '#64748B'
}

export function trendingEntryToRawItem(entry: TrendingEntry): RawItem {
  const metrics: Record<string, number> = {}
  if (entry.period_stars > 0) metrics.view = entry.period_stars
  if (entry.stars > 0) metrics.like = entry.stars
  if (entry.forks > 0) metrics.fork = entry.forks
  return {
    id: entry.raw_content_id ? String(entry.raw_content_id) : entry.repo,
    platform: 'github',
    contentType: 'repo',
    account: entry.repo,
    region: 'global',
    title: entry.repo,
    publishedAt: '',
    identity: 'user',
    excerpt: entry.description || undefined,
    topics: (entry.brief?.tags ?? []).slice(0, 3),
    brief: entry.brief ?? null,
    originalUrl: entry.url,
    metrics,
    previewMode: entry.raw_content_id ? 'full' : 'external_only',
  }
}
