/** Admin 看板与后端 DTO 对应的类型与展示工具 */

export interface CollectTarget {
  id: number
  platform: string
  target_type: string
  target_value: string
  display_name: string | null
  interval_minutes: number
  enabled: boolean
  tier: 'core' | 'general'
  community: 'wuwa' | 'blhx'
  co_terms: string[]
  min_views: number
  next_run_at: string | null
  last_run_at: string | null
  last_success_at: string | null
  consecutive_failures: number
  last_error: string | null
}

export interface Overview {
  targets: CollectTarget[]
  queue: { pending_jobs: number | null }
  total_contents: number
}

export interface ContentItem {
  id: number
  platform: string
  content_type: string
  external_id: string
  community: 'wuwa' | 'blhx'
  url: string | null
  published_at: string | null
  collected_at: string
  source_name: string | null
  title: string | null
  version: number | null
  latest_metrics: Record<string, number> | null
  hidden: boolean
  tier: 'core' | 'general' | 'none'
  matched_keywords: string[]
  review: {
    relevant: boolean
    promotional: boolean
    violation: boolean
    quality_score: number
    reason: string | null
    updated_at: string | null
  } | null
}

export interface ContentList {
  items: ContentItem[]
  total: number
}

export const platformLabels: Record<string, string> = {
  bilibili: 'B站',
  miyoushe: '米游社',
  kuro_cn: '库街区·国服',
  kuro_global: '库街区·国际服',
  youtube: 'YouTube',
}

export const targetTypeLabels: Record<string, string> = {
  keyword_search: '关键词搜索',
  account_videos: '账号视频',
  account_dynamics: '账号动态',
  notices: '官方公告',
  channel_rss: '频道 RSS',
}

export const contentTypeLabels: Record<string, string> = {
  video: '视频',
  dynamic: '动态',
  post: '帖子',
  notice: '公告',
  rss_item: 'RSS',
}

/** 后端时间为 naive UTC（无 Z 后缀），按北京时间展示 */
export function fmtTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
