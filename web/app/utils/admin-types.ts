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
  /** 游戏分区 key（合法值集合随分区注册表扩展，见 utils/admin-communities.ts） */
  community: string
  co_terms: string[]
  min_views: number
  /** 监控意图：自然语言描述这个目标在乎什么；空 = 不送 AI 打标 */
  intent: string | null
  next_run_at: string | null
  last_run_at: string | null
  last_success_at: string | null
  consecutive_failures: number
  last_error: string | null
}

export interface AdminUser {
  id: number
  email: string
  display_name: string | null
  avatar: string | null
  is_verified: boolean
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

export interface AdminUserDetail extends AdminUser {
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

// ---------- 规则引擎（批次 3a） ----------

export const ruleActionLabels: Record<string, string> = {
  tag: '打标签',
  promote_event: '转为事件',
  notify: '通知渠道',
  mark_read: '标记已读',
}

export const ruleChannelLabels: Record<string, string> = {
  webhook: 'Webhook',
  email: '邮件',
  qq: 'QQ 机器人',
}

export const intentTierLabels: Record<string, string> = {
  important: '重要',
  normal: '一般',
  ignore: '忽略',
}

export const sourceTierLabels: Record<string, string> = {
  high: '高权重',
  mid: '中权重',
  low: '低权重',
  unknown: '未分级',
}

export interface RuleAction {
  type: string
  value?: string | null
  channels?: string[]
}

export interface RuleEngineItem {
  id: number
  name: string
  enabled: boolean
  priority: number
  keyword_expr: string | null
  platforms: string[]
  communities: string[]
  intent_tiers: string[]
  actions: RuleAction[]
  note: string | null
  hit_count: number
  last_hit_at: string | null
  last_error: string | null
  created_at: string | null
  updated_at: string | null
  parsed_expr: string | null
}

export interface RuleEngineList {
  items: RuleEngineItem[]
  total: number
  page: number
  page_size: number
}

export interface RuleHitItem {
  id: number
  rule_id: number
  rule_name: string | null
  content_id: number
  content_title: string | null
  actions: RuleAction[]
  detail: string | null
  error: string | null
  created_at: string | null
}

export interface RuleHitList {
  items: RuleHitItem[]
  total: number
  page: number
  page_size: number
}

export interface VolumeBaselineItem {
  id: number
  group_key: string
  platform: string
  keyword: string
  target_id: number | null
  window_started_at: string
  window_minutes: number
  hit_count: number
  mean: number
  stddev: number
  samples: number
  multiplier: number
  status: string
  ratio: number | null
  notified_at: string | null
}

export interface RulesConfig {
  enabled: boolean
  anomaly_multiplier: number
  window_minutes: number
  history_windows: number
  min_samples: number
  min_hits: number
  cooldown_minutes: number
  source_tiers: { mid: number; high: number }
}
