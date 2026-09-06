<script setup lang="ts">
import { type Platform, type RawItem, type Region } from '~/utils/data'
import { toRawItem } from '~/utils/public-utils'
import { platformsForCommunity, useCommunityScope } from '~/composables/useGameSection'
import type { PublicContentList, PublicContentItem } from '~/composables/usePublicApi'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'cn', label: '国服' },
  { key: 'global', label: '国际服' },
  { key: 'bilibili', label: 'B站' },
  { key: 'miyoushe', label: '米游社' },
  { key: 'kuro', label: '库街区' },
  { key: 'youtube', label: 'YouTube' }
]
const route = useRoute()
const router = useRouter()
const { scope, isAll } = useCommunityScope()
// 平台筛选随视角变化（映射统一在 useGameSection）：聚合=all 并集，分区各自收敛
const visibleTabs = computed(() => {
  const allowed = platformsForCommunity(scope.value)
  return tabs.filter(t => t.key === 'all' || allowed.includes(t.key))
})
const tab = computed({
  get: () => String(route.query.tab || 'all'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
watch(scope, () => {
  if (!visibleTabs.value.some(t => t.key === tab.value)) tab.value = 'all'
})

const { api } = usePublicApi()
const { data, status } = await useAsyncData(
  () => `official-feed-${scope.value}`,
  () =>
    api<PublicContentList>('/api/contents', {
      query: { community: scope.value, identity: 'official', limit: 100 },
    }),
  { watch: [scope] },
)

function toRow(item: PublicContentItem): RawItem {
  return toRawItem(item)
}

const list = computed(() => {
  const rows = (data.value?.items ?? []).map(toRow)
  const k = tab.value
  if (k === 'all') return rows
  if (k === 'cn' || k === 'global')
    return rows.filter((row) => row.region === (k as Region))
  if (k === 'kuro')
    return rows.filter((row) => row.platform === 'kuro_cn' || row.platform === 'kuro_global')
  return rows.filter((row) => row.platform === (k as Platform))
})

useSeoMeta({ title: '官方动态' })
</script>

<template>
  <div>
    <BackToChannelHome />
    <TabNav v-model="tab" :tabs="visibleTabs" />

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <section v-else class="panel overflow-hidden">
      <RawFeedRow v-for="row in list" :key="row.id" :item="row" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此筛选下的官方内容。
      </p>
    </section>
  </div>
</template>
