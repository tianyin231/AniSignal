<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
import { platformMeta, type Platform, type RawItem, type Region } from '~/utils/data'
import type { PublicContentList, PublicContentItem, PublicStats } from '~/composables/usePublicApi'

const tabs = [
  { key: 'latest', label: '最新' },
  { key: 'official', label: '官方' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'latest'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30

const { api } = usePublicApi()
const feedQuery = computed(() => ({
  limit: pageSize,
  offset: (page.value - 1) * pageSize,
  ...(tab.value === 'official' ? { identity: 'official' } : {}),
}))
const { data: feed, status } = await useAsyncData(
  () => `home-feed-${tab.value}-${page.value}`,
  () => api<PublicContentList>('/api/contents', { query: feedQuery.value }),
  { watch: [tab, page] },
)
const { data: stats } = await useAsyncData('home-stats', () => api<PublicStats>('/api/stats'))

const totalPages = computed(() => Math.max(1, Math.ceil((feed.value?.total ?? 0) / pageSize)))

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
      account: item.source_name ?? '—',
      region: regionOf[item.platform] ?? 'cn',
      title: item.title || item.external_id,
      publishedAt: fmtTime(item.published_at || item.collected_at),
    },
    url: item.url,
  }
}
const list = computed(() => (feed.value?.items ?? []).map(toRow))

function goPage(p: number) {
  router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
}

useSeoMeta({ title: '今日情报' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="stats" class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-slate-400 dark:text-zinc-500">
      <span>已入库 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.total_contents }}</span> 条</span>
      <span>官方 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.official_contents }}</span> 条</span>
      <span v-if="stats.latest_collected_at">最近采集 {{ fmtTime(stats.latest_collected_at) }}</span>
    </section>

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
        在已接入来源中暂无内容。
      </p>
      <div class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ feed?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </section>

    <div class="mt-6 space-y-4 xl:hidden">
      <StatusCard />
      <ReportCard />
    </div>
  </div>
</template>
