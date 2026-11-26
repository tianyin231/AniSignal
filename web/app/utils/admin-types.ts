/** Admin 看板与后端 DTO 对应的类型与展示工具 */
import { ADMIN_PLATFORM_LABELS, CONTENT_TYPE_LABELS, TARGET_TYPE_LABELS } from './lexicon.ts'

export interface WebPageConfig {
  selector?: string
  exclude_selector?: string
  link_pattern?: string
  ignore_patterns?: string[]
  fetch_body?: boolean
}

export interface CollectTarget {
  id: number
  platform: string
  target_type: string
  target_value: string
  display_name: string | null
  interval_minutes: number
  enabled: boolean
  is_official: boolean
  web_config?: WebPageConfig
  tier: 'core' | 'general'
  /** 游戏分区 key（合法值集合随分区注册表扩展，见 utils/admin-communities.ts） */
  community: string
  co_terms: string[]
  min_views: number
  next_run_at: string | null
  last_run_at: string | null
  last_success_at: string | null
  total_items_new: number | null
  consecutive_failures: number
  last_error: string | null
}

export interface AdminUser {
  id: number
  email: string
  display_name: string | null
  avatar: string | null
  is_verified: boolean
  is_admin?: boolean
  notification_enabled: boolean
  status: 'active' | 'banned' | string
  banned_reason: string | null
  banned_at: string | null
  created_at: string
  last_login_at: string | null
  keyword_count?: number | null
}

export interface AdminUserList {
  items: AdminUser[]
  total: number
  page: number
  page_size: number
}

export interface AdminUserKeyword {
  id: number
  keyword: string
  enabled: boolean
  created_at: string
}

export interface AdminDelivery {
  id: number
  kind: string
  status: string
  subject: string | null
  error: string | null
  created_at: string
  sent_at: string | null
}

export interface AdminBotCapacity {
  max_bot_clients: number
  max_bot_targets: number
  bot_clients_used: number
  bot_targets_used: number
  default_max_bot_clients: number
  default_max_bot_targets: number
  overrides: { max_bot_clients: number | null, max_bot_targets: number | null }
}

export interface AdminUserDetail extends AdminUser {
  bot_capacity: AdminBotCapacity
  keywords: AdminUserKeyword[]
  sent_total: number
  recent_deliveries: AdminDelivery[]
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
  community: string
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

export const platformLabels: Record<string, string> = ADMIN_PLATFORM_LABELS

export const targetTypeLabels: Record<string, string> = TARGET_TYPE_LABELS

export const contentTypeLabels: Record<string, string> = CONTENT_TYPE_LABELS

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
