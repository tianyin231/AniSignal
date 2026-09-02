/**
 * 官方动态外链聚合：活动 / 奖励 / H5 等直达链接。
 * 侧栏与移动端横滑条共享同一份状态，首处使用时拉取一次。
 */

export interface OfficialLinkItem {
  url: string
  category: string
  title: string
  platform: string
  published_at: string | null
}

export function useOfficialLinks() {
  const items = useState<OfficialLinkItem[]>('official-links', () => [])
  const loadedCommunity = useState<string>('official-links-loaded', () => '')
  const loading = useState('official-links-loading', () => false)
  const pendingCommunity = useState<string>('official-links-pending', () => '')

  /** 拉取指定分区的官方外链；分区变化后自动重新拉取。 */
  async function fetchOnce(community: string = 'wuwa') {
    if (loadedCommunity.value === community) return
    if (loading.value) {
      // 正在拉别的分区时先记下最新意图，结束后补拉
      pendingCommunity.value = community
      return
    }
    loading.value = true
    try {
      const res = await $fetch<{ items: OfficialLinkItem[] }>('/api/official-links', {
        query: { community },
      })
      items.value = res.items
      loadedCommunity.value = community
    } catch { /* 链接聚合失败不影响主内容 */ } finally {
      loading.value = false
      const next = pendingCommunity.value
      pendingCommunity.value = ''
      if (next && next !== loadedCommunity.value) void fetchOnce(next)
    }
  }

  return { items, fetchOnce }
}
