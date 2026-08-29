<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import type { ApiEventList, PublicContentList, PublicStats } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { toAppEvent, toRawItem } from '~/utils/public-utils'

// 三个分区互不混用：聚合结论、官方内容、非官方社区线索。
const tabs = [
  { key: 'briefing', label: '最新情报' },
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
const pageSize = 20

const { api } = usePublicApi()

const eventsRequest = useAsyncData(
  () => `home-briefing-${page.value}`,
  () => api<ApiEventList>('/api/events', {
    query: { published_within_hours: 168, limit: pageSize, offset: (page.value - 1) * pageSize },
  }),
  { watch: [page] },
)
const feedRequest = useAsyncData(
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
const officialHighlightsRequest = useAsyncData('home-official-highlights', () =>
  api<PublicContentList>('/api/contents', {
    query: { identity: 'official', published_within_hours: 168, limit: 12 },
  }),
)
const communityYouTubeHighlightsRequest = useAsyncData('home-community-youtube-highlights', () =>
  api<PublicContentList>('/api/contents', {
    query: { platform: 'youtube', exclude_identity: 'official', published_within_hours: 168, limit: 1 },
  }),
)
const communityBilibiliHighlightsRequest = useAsyncData('home-community-bilibili-highlights', () =>
  api<PublicContentList>('/api/contents', {
    query: { platform: 'bilibili', exclude_identity: 'official', published_within_hours: 168, limit: 1 },
  }),
)
const statsRequest = useAsyncData('home-stats', () => api<PublicStats>('/api/stats'))

const [
  { data: eventsData, status: eventsStatus },
  { data: feed, status: feedStatus },
  { data: officialHighlightsData },
  { data: communityYouTubeHighlightsData },
  { data: communityBilibiliHighlightsData },
  { data: stats },
] = await Promise.all([
  eventsRequest,
  feedRequest,
  officialHighlightsRequest,
  communityYouTubeHighlightsRequest,
  communityBilibiliHighlightsRequest,
  statsRequest,
])

const isBriefing = computed(() => tab.value === 'briefing')
const totalPages = computed(() =>
  Math.max(1, Math.ceil(((isBriefing.value ? eventsData.value?.total : feed.value?.total) ?? 0) / pageSize)),
)
const eventList = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const list = computed(() => (feed.value?.items ?? []).map(toRawItem))
const recentOfficialItems = computed(() =>
  (officialHighlightsData.value?.items ?? []).map(toRawItem),
)
const featuredOfficial = computed(() =>
  recentOfficialItems.value.find((item) => item.cover) ?? recentOfficialItems.value[0],
)
const latestOfficialUpdate = computed(() => {
  const latest = recentOfficialItems.value[0]
  return latest && latest.id !== featuredOfficial.value?.id ? latest : null
})
const recentCommunityVideos = computed(() => {
  const rows = [
    ...(communityYouTubeHighlightsData.value?.items ?? []),
    ...(communityBilibiliHighlightsData.value?.items ?? []),
  ]
    .sort((a, b) => new Date(b.published_at || b.collected_at).getTime() - new Date(a.published_at || a.collected_at).getTime())
  return rows
    .slice(0, 4)
    .map(toRawItem)
})
const loading = computed(() => (isBriefing.value ? eventsStatus.value : feedStatus.value) === 'pending')
const refreshing = ref(false)

function goPage(p: number) {
  router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
}

async function refreshHome() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.all([
      eventsRequest.refresh(),
      feedRequest.refresh(),
      officialHighlightsRequest.refresh(),
      communityYouTubeHighlightsRequest.refresh(),
      communityBilibiliHighlightsRequest.refresh(),
      statsRequest.refresh(),
    ])
  }
  finally {
    refreshing.value = false
  }
}

let refreshTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  refreshTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void refreshHome()
  }, 60_000)
})
onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

useSeoMeta({ title: '近期情报' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="stats" class="mb-3 flex flex-wrap items-center justify-between gap-2 px-1 text-xs text-slate-400 dark:text-zinc-500">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span>已入库 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.total_contents }}</span> 条</span>
        <span>官方 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.official_contents }}</span> 条</span>
        <span v-if="stats.latest_collected_at">最近采集 {{ fmtTime(stats.latest_collected_at) }}</span>
      </div>
      <button
        type="button"
        :disabled="refreshing"
        class="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 text-xs text-slate-500 transition-colors hover:bg-slate-500/10 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-zinc-400 dark:hover:text-blue-300"
        @click="refreshHome"
      >
        <RefreshCw :size="13" :class="{ 'animate-spin': refreshing }" aria-hidden="true" />
        {{ refreshing ? '刷新中…' : '刷新' }}
      </button>
    </section>
    <section v-if="loading" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>

    <!-- 首页权重：官方发布 > 聚合结论 > 社区线索。 -->
    <template v-else-if="isBriefing">
      <section aria-labelledby="official-latest-title" class="mb-6">
        <header class="mb-3 flex items-end justify-between gap-4 px-1">
          <h1 id="official-latest-title" class="text-lg font-semibold text-slate-900 dark:text-zinc-50">官方最新情报</h1>
          <NuxtLink to="/?tab=official" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            全部官方动态
          </NuxtLink>
        </header>

        <NuxtLink
          v-if="latestOfficialUpdate"
          :to="`/contents/${latestOfficialUpdate.id}`"
          class="panel group mb-3 flex min-h-14 cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:border-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:border-blue-500/50"
        >
          <span class="shrink-0 rounded bg-blue-600 px-2 py-1 text-[11px] font-semibold text-white">最新发布</span>
          <span class="min-w-0 flex-1 truncate text-sm font-medium text-slate-800 group-hover:text-blue-700 dark:text-zinc-100 dark:group-hover:text-blue-300">
            {{ latestOfficialUpdate.title }}
          </span>
          <span class="mono hidden shrink-0 text-xs text-slate-400 sm:block">{{ latestOfficialUpdate.publishedAt }}</span>
        </NuxtLink>

        <OfficialFeatureCard v-if="featuredOfficial" :item="featuredOfficial" />
        <div v-else class="panel px-4 py-10 text-center text-sm text-slate-400">
          近 7 天暂无官方内容。
        </div>
      </section>

      <section aria-labelledby="briefing-title" class="mb-6">
        <header class="mb-3 flex items-end justify-between gap-4 px-1">
          <h2 id="briefing-title" class="text-base font-semibold text-slate-900 dark:text-zinc-50">近期聚合结论</h2>
          <NuxtLink to="/events" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">查看事件库</NuxtLink>
        </header>
        <div class="panel overflow-hidden">
          <EventRow v-for="e in eventList" :key="e.id" :event="e" />
          <p v-if="!eventList.length" class="px-4 py-10 text-center text-sm text-slate-400">
            近 7 天暂无新事件。
          </p>
          <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
            <span>共 {{ eventsData?.total ?? 0 }} 条</span>
            <div class="flex items-center gap-3">
              <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
              <span class="mono">{{ page }} / {{ totalPages }}</span>
              <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
            </div>
          </div>
        </div>
      </section>

      <section v-if="recentCommunityVideos.length" aria-labelledby="community-title" class="panel p-4">
        <header class="mb-3 flex items-start justify-between gap-4">
          <h2 id="community-title" class="text-sm font-semibold text-slate-800 dark:text-zinc-100">社区近期观察</h2>
          <NuxtLink to="/?tab=latest" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            查看社区线索
          </NuxtLink>
        </header>
        <div class="grid gap-3 sm:grid-cols-2">
          <OfficialVideoCard v-for="video in recentCommunityVideos" :key="video.id" :item="video" />
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
