<script setup lang="ts">
import type { ApiEventList, PublicContentList, PublicStats } from '~/composables/usePublicApi'
import { fmtTime, toAppEvent, toRawItem } from '~/utils/public-utils'

// 信息分层：要闻（事件结论）> 官方 > 来源动态（原始内容流，降噪后）
const tabs = [
  { key: 'briefing', label: '要闻' },
  { key: 'official', label: '官方' },
  { key: 'latest', label: '来源动态' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'briefing'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30

const { api } = usePublicApi()
const { t } = useSiteConfig()

const { data: eventsData, status: eventsStatus } = await useAsyncData(
  () => `home-briefing-${page.value}`,
  () => api<ApiEventList>('/api/events', { query: { limit: pageSize, offset: (page.value - 1) * pageSize } }),
  { watch: [page] },
)
const { data: feed, status: feedStatus } = await useAsyncData(
  () => `home-feed-${tab.value}-${page.value}`,
  () =>
    tab.value === 'latest' || tab.value === 'official'
      ? api<PublicContentList>('/api/contents', {
          query: {
            limit: pageSize,
            offset: (page.value - 1) * pageSize,
            ...(tab.value === 'official' ? { identity: 'official' } : {}),
          },
        })
      : Promise.resolve({ items: [], total: 0 }),
  { watch: [tab, page] },
)
const { data: stats } = await useAsyncData('home-stats', () => api<PublicStats>('/api/stats'))
const { data: hotData } = await useAsyncData('home-hot', () =>
  api<PublicContentList>('/api/contents', { query: { sort: 'hot', limit: 12 } }),
)
const communityPulse = computed(() =>
  (hotData.value?.items ?? [])
    .filter((i) => !['official', 'seeded'].includes(i.identity))
    .slice(0, 5),
)

const isBriefing = computed(() => tab.value === 'briefing')
const totalPages = computed(() =>
  Math.max(1, Math.ceil(((isBriefing.value ? eventsData.value?.total : feed.value?.total) ?? 0) / pageSize)),
)
const eventList = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const list = computed(() => (feed.value?.items ?? []).map(toRawItem))
const loading = computed(() => (isBriefing.value ? eventsStatus.value : feedStatus.value) === 'pending')

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
    <p class="mb-3 px-1 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">{{ t('home_intro') }}</p>

    <section v-if="loading" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>

    <!-- 要闻：事件结论流（默认） -->
    <section v-else-if="isBriefing" class="panel overflow-hidden">
      <EventRow v-for="e in eventList" :key="e.id" :event="e" />
      <p v-if="!eventList.length" class="px-4 py-10 text-center text-sm text-slate-400">
        事件整理中；引擎会随采集持续聚合官方公告与商品履约变化。
      </p>
      <!-- 社区动向：命中监控关键词的热门用户内容 -->
      <div v-if="communityPulse.length" class="border-t border-slate-200/70 px-4 py-3 dark:border-zinc-800/60">
        <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">社区动向 · 用户热议</p>
        <div class="mt-1.5 space-y-1">
          <NuxtLink v-for="i in communityPulse" :key="i.id" :to="`/contents/${i.id}`" class="flex items-baseline justify-between gap-3 text-[13px] leading-relaxed">
            <span class="min-w-0 truncate text-slate-600 transition-colors hover:text-blue-600 dark:text-zinc-300 dark:hover:text-blue-300">{{ i.title }}</span>
            <span class="mono shrink-0 text-xs text-slate-400">{{ ((i.metrics || {}).view ?? 0).toLocaleString() }} 播放</span>
          </NuxtLink>
        </div>
      </div>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ eventsData?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </section>

    <!-- 官方 / 来源动态：内容流 -->
    <section v-else class="panel overflow-hidden">
      <NuxtLink v-for="entry in list.map((row) => ({ row }))" :key="entry.row.id" :to="`/contents/${entry.row.id}`" class="block">
        <RawFeedRow :item="entry.row" />
      </NuxtLink>
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无内容。
      </p>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
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
      <TodayBriefCard />
    </div>
  </div>
</template>
