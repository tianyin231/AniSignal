/**
 * 官方动态外链聚合：活动 / 奖励 / H5 等直达链接。
 * 侧栏与移动端横滑条共享同一份状态，首处使用时拉取一次。
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
// 仅客户端发起请求，Promise 不进入 SSR 状态；侧栏与移动端条共享在途请求。
const pendingRequests = new Map<string, Promise<void>>()

export function useOfficialLinks(community: Ref<string>) {
  const { api } = usePublicApi()
  const cache = useState<Record<string, { items: OfficialLinkItem[], expiresAt: number }>>('official-links-cache', () => ({}))
  const items = computed(() => cache.value[community.value]?.items ?? [])

  async function fetchOnce() {
    if (import.meta.server) return
    const key = community.value
    if ((cache.value[key]?.expiresAt || 0) > Date.now()) return
    if (pendingRequests.has(key)) return pendingRequests.get(key)
    const request = api<{ items: OfficialLinkItem[] }>('/api/official-links', { query: { community: key } })
      .then(res => { cache.value[key] = { items: res.items, expiresAt: Date.now() + CACHE_TTL } })
      .catch(() => { /* 保留本分区旧结果，下一轮重试。 */ })
      .finally(() => { pendingRequests.delete(key) })
    pendingRequests.set(key, request)
    return request
  }

  return { items, fetchOnce }
}
