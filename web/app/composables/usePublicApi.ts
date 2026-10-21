/**
 * 公开 API 客户端（无需鉴权）：内容流、来源健康、统计。
 */

export interface PublicContentItem {
  id: number
  platform: string
  content_type: string
  external_id: string
  url: string | null
  title: string | null
  text_excerpt: string | null
  published_at: string | null
  collected_at: string
  source_name: string | null
  identity: string
  category: string | null
  metrics: Record<string, number> | null
  cover: string | null
  preview_mode: 'full' | 'partial' | 'external_only'
  matched_keywords: string[]
  community?: string
  delta_view?: number | null
}

export interface PublicContentList {
  items: PublicContentItem[]
  total: number
  /** 账号中心关注流附加：近 24 小时新采集的命中条数 */
  new_today?: number | null
}

export interface CalendarEntry {
  id: number
  content_id: number
  title: string
  kind: string
  start_at: string | null
  end_at: string | null
  all_day: boolean
  delayed: boolean
  community?: string
  url: string | null
}

export interface CalendarDto {
  ongoing: CalendarEntry[]
  upcoming: CalendarEntry[]
  recent: CalendarEntry[]
  ended: CalendarEntry[]
}

export interface RedeemCodeDto {
  code: string
  community?: string
  expires_at: string | null
  created_at: string
  url: string | null
}

export interface CodesDto {
  items: RedeemCodeDto[]
  total: number
}

export interface PublicSource {
  name: string
  platform: string
  target_type: string
  enabled: boolean
  last_success_at: string | null
  consecutive_failures: number
  community?: string
}

export interface PublicStats {
  total_contents: number
  by_platform: Record<string, number>
  official_contents: number
  latest_collected_at: string | null
}

export interface StatisticsOverview {
  range_days: number
  generated_at: string
  details_since: string | null
  totals: {
    total_contents: number
    official_contents: number
    community_contents: number
    period_new: number
    collection_runs: number
    failed_runs: number
    success_rate: number
    items_found: number
    items_new: number
    yield_rate: number
    latest_collection_at: string | null
  }
  trend: Array<{ date: string; total: number; official: number; community: number }>
  platforms: Array<{ platform: string; total: number; official: number; community: number; share: number }>
  identities: Array<{ identity: string; total: number; share: number }>
  keywords: Array<{ keyword: string; contents: number }>
  collection: Array<{
    date: string
    runs: number
    failed: number
    items_found: number
    items_new: number
    detailed_runs: number
    items_fetched: number
    items_filtered: number
    items_updated: number
  }>
  targets: Array<{
    target_id: number
    label: string
    platform: string
    target_type: string
    runs: number
    items_found: number
    items_new: number
    yield_rate: number
  }>
  recent_runs: Array<{
    id: number
    target: string
    platform: string
    status: string
    started_at: string
    finished_at: string | null
    duration_ms: number | null
    items_fetched: number | null
    items_filtered: number | null
    items_found: number
    items_new: number
    items_updated: number | null
  }>
}

export interface ApiEventSource {
  platform: string
  account: string | null
  title: string | null
  region: string
  published_at: string | null
  url: string | null
  content_id: number | null
  identity?: string
}

export interface ApiEventCorrection {
  at: string
  actor: string
  note: string
}

export interface ApiEvent {
  id: number
  title: string
  summary: string | null
  summary_status: string | null
  summary_updated_at: string | null
  category: string
  product_id: number | null
  product_slug: string | null
  identity: string
  importance: boolean
  manual_locked: boolean
  confidence: string
  confidence_reason: string | null
  conclusion: string | null
  status_label: string | null
  facts: { label: string; value: string }[]
  matched_keywords: string[]
  regions: string[]
  evidence_count: number
  community?: string
  sources: ApiEventSource[]
  conflicts: ApiEventSource[]
  corrections: ApiEventCorrection[]
  first_seen_at: string | null
  latest_evidence_at: string | null
  updated_at: string | null
}

export interface ApiEventList {
  items: ApiEvent[]
  total: number
}

export interface ApiProductStage {
  label: string
  state: 'done' | 'current' | 'pending'
  tone: string | null
  date: string | null
  evidence: number | null
  note: string | null
}

export interface FulfillmentAxis {
  key: 'official' | 'consumer' | 'quality'
  label: string
  state: string
  state_label: string
  summary: string
  verified_count: number
  automated_count?: number
  manual_count?: number
  candidate_count: number
  updated_at: string | null
  signal_ids: number[]
  ai_preview: string
}

export interface FulfillmentSnapshot {
  official: FulfillmentAxis
  consumer: FulfillmentAxis
  quality: FulfillmentAxis
  sample_count: number
  candidate_total: number
  automated_count?: number
  manual_count?: number
  observation_count?: number
  automation_mode?: 'automatic'
  last_verified_at: string | null
  updated_at?: string | null
}

export interface ApiProductSignal {
  id: number
  product_id: number
  community?: string
  signal_type: string
  signal_label: string
  actor_type: string
  review_status: 'candidate' | 'verified' | 'rejected' | 'conflict'
  review_method?: 'ai' | 'rule' | 'manual' | 'pending'
  review_reason?: string
  ai_hint?: { kind: string; confidence: number; reason: string } | null
  occurred_at: string | null
  summary: string
  confidence: number
  source_method: string
  acquisition_type: 'unknown' | 'self_purchase' | 'seeded'
  claim_quote: string | null
  content_id: number
  content_title: string | null
  platform: string
  account: string | null
  url: string | null
  review_note: string | null
  updated_at: string | null
}

export interface ApiProductSignalList {
  items: ApiProductSignal[]
  total: number
}

export interface ProductBrief {
  archived: boolean
  unboxing_accounts: number
  unboxing_from: string | null
  unboxing_to: string | null
  unboxing_note: string
  quality_accounts: number
  quality_latest: string | null
  official_text: string
  official_registered: boolean
}

export interface ApiProduct {
  id: number
  slug: string
  name: string
  community?: string
  publisher: string | null
  skus: string[]
  price_note: string | null
  presale_at: string | null
  balance_window: string | null
  original_ship_at: string | null
  ship_note: string | null
  watch_terms: string[]
  monitor_status?: 'watching' | 'archived'
  source_url?: string | null
  brief?: ProductBrief
  monitor?: {
    target_id: number
    keyword: string
    enabled: boolean
    interval_minutes: number
    min_views: number
    tier: string
  } | null
  stage_hint: number
  current_stage: number
  stage_label: string
  stage_source: 'signals' | 'unverified' | 'automatic' | 'ai' | 'manual'
  stage_reason: string
  stage_updated_at: string | null
  stage_evidence_ids: number[]
  official_status: string
  official_evidence_count: number
  consumer_status: string
  consumer_evidence_count: number
  evidences: ApiProductEvidence[]
  stages: ApiProductStage[]
  fulfillment: FulfillmentSnapshot
  latest_candidate?: {
    summary: string
    signal_label: string
    occurred_at: string | null
    content_id: number
  } | null
  updated_at: string | null
}

export interface ApiProductEvidence {
  content_id: number
  title: string | null
  platform: string
  account: string | null
  identity: string
  category: string | null
  published_at: string | null
  url: string | null
}

export interface ApiProductList {
  items: ApiProduct[]
}

export interface ContentMetricPoint {
  captured_at: string
  metrics: Record<string, number>
}

export interface ContentDetail {
  id: number
  community: string
  news: { headline: string; summary: string; kind: 'report' | 'rumor' } | null
  platform: string
  content_type: string
  external_id: string
  url: string | null
  title: string | null
  text: string | null
  text_html: string | null
  text_truncated: boolean
  published_at: string | null
  collected_at: string
  last_seen_at: string | null
  source_name: string | null
  source_identity: string | null
  identity: string
  media: string[]
  metrics: Record<string, number> | null
  metrics_history: ContentMetricPoint[]
  event: { id: number; title: string; category: string; confidence: string } | null
  translation: { lang: string; text: string; model: string | null } | null
  matched_keywords: string[]
  version: number | null
  preview_mode: 'full' | 'partial' | 'external_only'
}

export function usePublicApi() {
  const config = useRuntimeConfig()
  // SSR 直连 FastAPI；浏览器走同源（Nitro routeRules 代理）
  const baseURL = import.meta.server ? config.apiSsrBase : config.public.apiBase
  const api = $fetch.create({ baseURL, timeout: 15_000, retry: 0 })
  return { api }
}
