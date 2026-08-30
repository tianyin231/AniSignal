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
  const loaded = useState('official-links-loaded', () => false)
  const loading = useState('official-links-loading', () => false)

  async function fetchOnce() {
    if (loaded.value || loading.value) return
    loading.value = true
    try {
      const res = await $fetch<{ items: OfficialLinkItem[] }>('/api/official-links')
      items.value = res.items
      loaded.value = true
    } catch { /* 链接聚合失败不影响主内容 */ } finally {
      loading.value = false
    }
  }

  return { items, loaded, fetchOnce }
}
