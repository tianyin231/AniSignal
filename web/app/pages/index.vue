<script setup lang="ts">
import type { ApiEventList, PublicContentList, PublicStats } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { toAppEvent, toRawItem } from '~/utils/public-utils'

// 三个分区互不混用：聚合结论、官方内容、非官方社区线索。
const tabs = [
  { key: 'briefing', label: '近期结论' },
  { key: 'official', label: '官方内容' },
  { key: 'latest', label: '社区线索' },
]
const validTabs = new Set(tabs.map((item) => item.key))
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => {
    const value = String(route.query.tab || 'briefing')
    return validTabs.has(value) ? value : 'briefing'
  },
  set: (v: string) => router.replace({ query: { ...route.query, tab: v, page: undefined } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30

const { api } = usePublicApi()
const { t } = useSiteConfig()

const { data: eventsData, status: eventsStatus } = await useAsyncData(
  () => `home-briefing-${page.value}`,
  () => api<ApiEventList>('/api/events', {
    query: { published_within_hours: 168, limit: pageSize, offset: (page.value - 1) * pageSize },
  }),
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
            ...(tab.value === 'official'
              ? { identity: 'official', published_within_hours: 168 }
              : { exclude_identity: 'official', published_within_hours: 72 }),
          },
        })
      : Promise.resolve({ items: [], total: 0 }),
  { watch: [tab, page] },
)
const { data: stats } = await useAsyncData('home-stats', () => api<PublicStats>('/api/stats'))

const isBriefing = computed(() => tab.value === 'briefing')
const totalPages = computed(() =>
  Math.max(1, Math.ceil(((isBriefing.value ? eventsData.value?.total : feed.value?.total) ?? 0) / pageSize)),
)
const eventList = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const list = computed(() => (feed.value?.items ?? []).map(toRawItem))
const loading = computed(() => (isBriefing.value ? eventsStatus.value : feedStatus.value) === 'pending')
const sectionHint = computed(() => ({
  briefing: '近 7 天内出现新证据的聚合事件；摘要受证据约束，点击可核对原文。',
  official: '近 7 天官方账号与官网发布的内容；正文、图片与仅外链状态会明确标注。',
  latest: '近 72 小时非官方来源内容，仅作线索，不代表事实结论。',
})[tab.value])

function goPage(p: number) {
  router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
}

useSeoMeta({ title: '近期情报' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="stats" class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-slate-400 dark:text-zinc-500">
      <span>已入库 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.total_contents }}</span> 条</span>
      <span>官方 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.official_contents }}</span> 条</span>
      <span v-if="stats.latest_collected_at">最近采集 {{ fmtTime(stats.latest_collected_at) }}</span>
    </section>
    <p class="mb-1 px-1 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">{{ t('home_intro') }}</p>
    <p class="mb-3 px-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ sectionHint }}</p>

    <section v-if="loading" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>

    <!-- 近期结论：只展示近 7 天内有新证据的事件 -->
    <section v-else-if="isBriefing" class="panel overflow-hidden">
      <EventRow v-for="e in eventList" :key="e.id" :event="e" />
      <p v-if="!eventList.length" class="px-4 py-10 text-center text-sm text-slate-400">
        近 7 天暂无可核对的新事件；可到“官方内容”查看同期内容。
      </p>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ eventsData?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </section>

    <!-- 官方内容 / 社区线索：严格按身份和发布时间拆分 -->
    <section v-else class="panel overflow-hidden">
      <RawFeedRow v-for="row in list" :key="row.id" :item="row" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        {{ tab === 'official' ? '近 7 天暂无官方内容。' : '近 72 小时暂无社区线索。' }}
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
