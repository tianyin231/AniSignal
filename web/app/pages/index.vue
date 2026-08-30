<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import type { ApiEventList, PublicContentList } from '~/composables/usePublicApi'
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
  { key: 'kuro', label: '库街区' },
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
const highlightsRequest = useAsyncData('home-highlights', () =>
  api<PublicContentList>('/api/highlights', { query: { limit: 5 } }),
)
const communityHighlightsRequest = useAsyncData('home-community-highlights', () =>
  api<PublicContentList>('/api/contents', {
    query: { exclude_identity: 'official', published_within_hours: 168, sort: 'hot', limit: 8 },
  }),
)
const [
  { data: eventsData, status: eventsStatus },
  { data: feed, status: feedStatus },
  { data: highlightsData },
  { data: communityHighlightsData },
] = await Promise.all([
  eventsRequest,
  feedRequest,
  highlightsRequest,
  communityHighlightsRequest,
])

const isBriefing = computed(() => tab.value === 'briefing')
const totalPages = computed(() =>
  Math.max(1, Math.ceil(((isBriefing.value ? eventsData.value?.total : feed.value?.total) ?? 0) / pageSize)),
)
const eventList = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const list = computed(() => (feed.value?.items ?? []).map(toRawItem))
const featuredHighlights = computed(() =>
  (highlightsData.value?.items ?? []).map(toRawItem),
)
const recentCommunityVideos = computed(() => {
  const featuredIds = new Set(featuredHighlights.value.map(item => item.id))
  return (communityHighlightsData.value?.items ?? [])
    .filter(item => !featuredIds.has(String(item.id)))
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
      highlightsRequest.refresh(),
      communityHighlightsRequest.refresh(),
    ])
  }
  finally {
    refreshing.value = false
  }
}

// 温和的新内容探测：绝不自动改写正在看的列表，只在后台对比当前分区第一条，
// 有差异时亮出「有新内容」提示，由用户决定何时更新。
const pendingNewContent = ref(false)
let updateTimer: ReturnType<typeof setInterval> | undefined

function currentHeadId(): string | null {
  if (isBriefing.value) {
    const head = eventList.value[0]?.id
    return head == null ? null : String(head)
  }
  const head = list.value[0]?.id
  return head == null ? null : String(head)
}

async function checkForUpdates() {
  if (document.visibilityState !== 'visible') return
  if (pendingNewContent.value || loading.value || refreshing.value) return
  try {
    let head: string | null = null
    if (isBriefing.value) {
      const res = await $fetch<ApiEventList>('/api/events', {
        query: { published_within_hours: 168, limit: 1 },
      })
      head = res.items[0]?.id != null ? String(res.items[0].id) : null
    } else if (tab.value === 'official' || tab.value === 'latest') {
      const res = await $fetch<PublicContentList>('/api/contents', {
        query: {
          limit: 1,
          ...(tab.value === 'official'
            ? {
                identity: 'official',
                published_within_hours: 168,
                ...(officialPlatform.value !== 'all' ? { platform: officialPlatform.value } : {}),
              }
            : { exclude_identity: 'official', published_within_hours: 72 }),
        },
      })
      head = res.items[0]?.id != null ? String(res.items[0].id) : null
    } else {
      return
    }
    const current = currentHeadId()
    if (head && current && head !== current) pendingNewContent.value = true
  } catch { /* 探测失败静默跳过 */ }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') void checkForUpdates()
}

async function applyUpdates() {
  pendingNewContent.value = false
  await refreshHome()
}

watch([tab, officialPlatform, page], () => {
  pendingNewContent.value = false
})

onMounted(() => {
  updateTimer = setInterval(checkForUpdates, 60_000)
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => {
  if (updateTimer) clearInterval(updateTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

useSeoMeta({ title: '近期情报' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs">
      <template #actions>
        <button
          v-if="pendingNewContent"
          type="button"
          class="inline-flex min-h-9 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full bg-blue-600/10 px-3 py-1.5 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-600/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:bg-blue-400/15 dark:text-blue-300"
          @click="applyUpdates"
        >
          <span class="size-1.5 animate-pulse rounded-full bg-blue-500" aria-hidden="true" />
          有新内容
        </button>
        <button
          type="button"
          :disabled="refreshing"
          class="inline-flex min-h-9 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-[10px] px-3 py-1.5 text-xs text-slate-500 transition-colors hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-wait disabled:opacity-60 dark:text-zinc-400 dark:hover:text-blue-300"
          @click="pendingNewContent ? applyUpdates() : refreshHome()"
        >
          <RefreshCw :size="13" :class="{ 'animate-spin': refreshing }" aria-hidden="true" />
          {{ refreshing ? '刷新中…' : '刷新' }}
        </button>
      </template>
    </TabNav>
    <section v-if="loading" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>

    <!-- 首页权重：官方与社区精选 > 聚合结论 > 社区线索。 -->
    <template v-else-if="isBriefing">
      <section aria-labelledby="official-latest-title" class="mb-6">
        <header class="mb-3 flex items-end justify-between gap-4 px-1">
          <h1 id="official-latest-title" class="text-lg font-semibold text-slate-900 dark:text-zinc-50">最新情报</h1>
          <NuxtLink to="/contents" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            查看内容库
          </NuxtLink>
        </header>

        <FeaturedCarousel v-if="featuredHighlights.length" :items="featuredHighlights" />
        <div v-else class="panel px-4 py-10 text-center text-sm text-slate-400">
          近 7 天暂无精选内容。
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
      </div>
  </div>
</template>
