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
const officialPlatformTabs = [
  { key: 'all', label: '全部' },
  { key: 'bilibili', label: 'B站' },
  { key: 'youtube', label: 'YouTube' },
  { key: 'kuro', label: '官网' },
]
const validOfficialPlatforms = new Set(officialPlatformTabs.map((item) => item.key))
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => {
    const value = String(route.query.tab || 'briefing')
    return validTabs.has(value) ? value : 'briefing'
  },
  set: (v: string) => router.replace({ query: { ...route.query, tab: v, page: undefined } }),
})
const officialPlatform = computed({
  get: () => {
    const value = String(route.query.platform || 'all')
    return validOfficialPlatforms.has(value) ? value : 'all'
  },
  set: (value: string) => router.replace({
    query: { ...route.query, platform: value === 'all' ? undefined : value, page: undefined },
  }),
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
  () => `home-feed-${tab.value}-${officialPlatform.value}-${page.value}`,
  () =>
    tab.value === 'latest' || tab.value === 'official'
      ? api<PublicContentList>('/api/contents', {
          query: {
            limit: pageSize,
            offset: (page.value - 1) * pageSize,
            ...(tab.value === 'official'
              ? {
                  identity: 'official',
                  published_within_hours: 168,
                  ...(officialPlatform.value !== 'all' ? { platform: officialPlatform.value } : {}),
                }
              : { exclude_identity: 'official', published_within_hours: 72 }),
          },
        })
      : Promise.resolve({ items: [], total: 0 }),
  { watch: [tab, officialPlatform, page] },
)
const { data: officialVideosData } = await useAsyncData('home-official-videos', () =>
  api<PublicContentList>('/api/contents', {
    query: { identity: 'official', published_within_hours: 168, limit: 100 },
  }),
)
const { data: stats } = await useAsyncData('home-stats', () => api<PublicStats>('/api/stats'))

const isBriefing = computed(() => tab.value === 'briefing')
const totalPages = computed(() =>
  Math.max(1, Math.ceil(((isBriefing.value ? eventsData.value?.total : feed.value?.total) ?? 0) / pageSize)),
)
const eventList = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const list = computed(() => (feed.value?.items ?? []).map(toRawItem))
const recentOfficialVideos = computed(() => {
  const rows = (officialVideosData.value?.items ?? [])
    .filter((item) => ['bilibili', 'youtube'].includes(item.platform))
    .sort((a, b) => new Date(b.published_at || b.collected_at).getTime() - new Date(a.published_at || a.collected_at).getTime())
  const selected = ['youtube', 'bilibili'].flatMap((platform) =>
    rows.filter((item) => item.platform === platform).slice(0, 2),
  )
  if (selected.length < 4) {
    const selectedIds = new Set(selected.map((item) => item.id))
    selected.push(...rows.filter((item) => !selectedIds.has(item.id)).slice(0, 4 - selected.length))
  }
  return selected
    .sort((a, b) => new Date(b.published_at || b.collected_at).getTime() - new Date(a.published_at || a.collected_at).getTime())
    .slice(0, 4)
    .map(toRawItem)
})
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

    <!-- 近期结论：平台入口先可见，再展示近 7 天内有新证据的事件 -->
    <template v-else-if="isBriefing">
      <section v-if="recentOfficialVideos.length" class="panel mb-4 p-4">
        <header class="mb-3 flex items-start justify-between gap-4">
          <div>
            <h2 class="text-sm font-semibold text-slate-800 dark:text-zinc-100">最新官方影像</h2>
            <p class="mt-0.5 text-xs text-slate-500 dark:text-zinc-400">YouTube 视频与 B站官方动态，点击查看站内解析。</p>
          </div>
          <NuxtLink to="/?tab=official&platform=youtube" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            查看 YouTube
          </NuxtLink>
        </header>
        <div class="grid gap-3 sm:grid-cols-2">
          <OfficialVideoCard v-for="video in recentOfficialVideos" :key="video.id" :item="video" />
        </div>
      </section>

      <section class="panel overflow-hidden">
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
    </template>

    <!-- 官方内容 / 社区线索：严格按身份和发布时间拆分 -->
    <template v-else>
      <nav v-if="tab === 'official'" aria-label="官方内容平台筛选" class="mb-3 flex flex-wrap gap-1.5">
        <button
          v-for="item in officialPlatformTabs"
          :key="item.key"
          type="button"
          class="min-h-9 cursor-pointer rounded-full px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          :class="officialPlatform === item.key
            ? 'bg-blue-600 text-white'
            : 'bg-slate-500/10 text-slate-600 hover:bg-slate-500/15 hover:text-slate-800 dark:text-zinc-300 dark:hover:text-white'"
          :aria-pressed="officialPlatform === item.key"
          @click="officialPlatform = item.key"
        >
          {{ item.label }}
        </button>
      </nav>
      <section class="panel overflow-hidden">
        <RawFeedRow v-for="row in list" :key="row.id" :item="row" />
        <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
          {{ tab === 'official' ? '近 7 天暂无此平台的官方内容。' : '近 72 小时暂无社区线索。' }}
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
    </template>

    <div class="mt-6 space-y-4 xl:hidden">
      <StatusCard />
      <TodayBriefCard />
    </div>
  </div>
</template>
