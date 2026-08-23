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
  matched_keywords: string[]
}

export interface PublicContentList {
  items: PublicContentItem[]
  total: number
}

export interface PublicSource {
  name: string
  platform: string
  target_type: string
  enabled: boolean
  last_success_at: string | null
  consecutive_failures: number
}

export interface PublicStats {
  total_contents: number
  by_platform: Record<string, number>
  official_contents: number
  latest_collected_at: string | null
}

export interface ApiEventSource {
  platform: string
  account: string | null
  title: string | null
  region: string
  published_at: string | null
  url: string | null
  content_id: number | null
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
  sources: ApiEventSource[]
  conflicts: ApiEventSource[]
  corrections: ApiEventCorrection[]
  first_seen_at: string | null
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

export interface ApiProduct {
  id: number
  slug: string
  name: string
  publisher: string | null
  skus: string[]
  price_note: string | null
  presale_at: string | null
  balance_window: string | null
  original_ship_at: string | null
  ship_note: string | null
  watch_terms: string[]
  stage_hint: number
  current_stage: number
  stage_label: string
  stages: ApiProductStage[]
  updated_at: string | null
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
  platform: string
  content_type: string
  external_id: string
  url: string | null
  title: string | null
  text: string | null
  text_html: string | null
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
}

export function usePublicApi() {
  const config = useRuntimeConfig()
  // SSR 直连 FastAPI；浏览器走同源（Nitro routeRules 代理）
  const baseURL = import.meta.server ? config.apiSsrBase : config.public.apiBase
  const api = $fetch.create({ baseURL })
  return { api }
}
