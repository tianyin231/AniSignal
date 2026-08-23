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
  metrics: Record<string, number> | null
  cover: string | null
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

export function usePublicApi() {
  const config = useRuntimeConfig()
  // SSR 直连 FastAPI；浏览器走同源（Nitro routeRules 代理）
  const baseURL = import.meta.server ? config.apiSsrBase : config.public.apiBase
  const api = $fetch.create({ baseURL })
  return { api }
}
