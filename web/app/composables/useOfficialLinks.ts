/**
 * 官方动态外链聚合：活动 / 奖励 / H5 等直达链接。
 * 频道头图与侧栏共享分区缓存；服务端预取后随页面传给客户端。
 */

import type { Ref } from 'vue'

export interface OfficialLinkItem {
  url: string
  category: string
  title: string
  /** AI 审核时提取的链接名称（如"鸣潮官网"），缺失时回退 title */
  label?: string
  platform: string
  published_at: string | null
}

const CACHE_TTL = 5 * 60_000
// 仅客户端共享在途请求；服务端缓存由 useState 隔离，避免跨请求共用 Promise。
const pendingRequests = new Map<string, Promise<void>>()

export function useOfficialLinks(community: Ref<string>) {
  const { api } = usePublicApi()
  const cache = useState<Record<string, { items: OfficialLinkItem[], expiresAt: number }>>('official-links-cache', () => ({}))
  const items = computed(() => cache.value[community.value]?.items ?? [])

  async function fetchOnce() {
    const key = community.value
    if ((cache.value[key]?.expiresAt || 0) > Date.now()) return
    if (import.meta.client && pendingRequests.has(key)) return pendingRequests.get(key)
    const request = api<{ items: OfficialLinkItem[] }>('/api/official-links', { query: { community: key } })
      .then(res => { cache.value[key] = { items: res.items, expiresAt: Date.now() + CACHE_TTL } })
      .catch(() => { /* 保留本分区旧结果，下一轮重试。 */ })
      .finally(() => { if (import.meta.client) pendingRequests.delete(key) })
    if (import.meta.client) pendingRequests.set(key, request)
    return request
  }

  return { items, fetchOnce }
}
