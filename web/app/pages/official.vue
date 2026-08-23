<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
import { platformMeta, type Platform, type RawItem, type Region } from '~/utils/data'
import type { PublicContentList, PublicContentItem } from '~/composables/usePublicApi'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'cn', label: '国服' },
  { key: 'global', label: '国际服' },
  { key: 'bilibili', label: 'B站' },
  { key: 'miyoushe', label: '米游社' },
  { key: 'kuro', label: '官网' },
  { key: 'youtube', label: 'YouTube' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'all'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})

const { api } = usePublicApi()
const { data, status } = await useAsyncData('official-feed', () =>
  api<PublicContentList>('/api/contents', { query: { identity: 'official', limit: 100 } }),
)

const regionOf: Record<string, Region> = {
  bilibili: 'cn',
  miyoushe: 'cn',
  kuro_cn: 'cn',
  kuro_global: 'global',
  youtube: 'global',
}

function toRow(item: PublicContentItem): { row: RawItem; url: string | null } {
  return {
    row: {
      id: String(item.id),
      platform: item.platform as Platform,
      account: item.source_name ?? '官方',
      region: regionOf[item.platform] ?? 'cn',
      title: item.title || item.external_id,
      publishedAt: fmtTime(item.published_at || item.collected_at),
    },
    url: item.url,
  }
}

const list = computed(() => {
  const rows = (data.value?.items ?? []).map(toRow)
  const k = tab.value
  if (k === 'all') return rows
  if (k === 'cn' || k === 'global')
    return rows.filter(({ row }) => row.region === (k as Region))
  if (k === 'kuro')
    return rows.filter(({ row }) => row.platform === 'kuro_cn' || row.platform === 'kuro_global')
  return rows.filter(({ row }) => row.platform === (k as Platform))
})

useSeoMeta({ title: '官方动态' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <p class="mb-3 text-xs text-slate-400 dark:text-zinc-500">
      已接入官方来源的原始内容流；跨平台重复公告的合并视图将在事件引擎上线后提供。
    </p>

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <section v-else class="panel overflow-hidden">
      <component
        :is="entry.url ? 'a' : 'div'"
        v-for="entry in list"
        :key="entry.row.id"
        v-bind="entry.url ? { href: entry.url, target: '_blank', rel: 'noopener' } : {}"
        class="block"
      >
        <RawFeedRow :item="entry.row" />
      </component>
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此筛选下的官方内容。
      </p>
    </section>
  </div>
</template>
