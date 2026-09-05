<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import type { ApiEventList, PublicContentList, PublicContentItem } from '~/composables/usePublicApi'
import { toAppEvent, toRawItem } from '~/utils/public-utils'

useSeoMeta({ title: '首页 · AniSignal' })

const route = useRoute()
const router = useRouter()
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
// 官方内容在混合流中占约 1/3（官方:社区 = 15:30 每页），官方比重由此保证
const OFFICIAL_PER_PAGE = 15
const COMMUNITY_PER_PAGE = 30
const OFFICIAL_BOOST_HOURS = 36
const HOT_BOOST_HOURS = 12

const { community, communityQuery } = useGameSection()
const { api } = usePublicApi()

// 站点栏目导航：与刷新同处一行（顶栏只有频道一层）
const sectionLinks = [
  { to: '/official', label: '官方动态' },
  { to: '/products', label: '商品' },
  { to: '/events', label: '事件' },
  { to: '/contents', label: '内容库' },
]
const isActive = (to: string) => route.path.startsWith(to)
// 首页频道语义：/ 无 community 参数 = 跨分区聚合（community=all）；带参数 = 该分区首页
const aggregate = computed(() => !route.query.community)
const homeCommunity = computed(() => (aggregate.value ? 'all' : community.value))

const eventsRequest = useAsyncData(
  () => `home-events-${homeCommunity.value}`,
  () => api<ApiEventList>('/api/events', {
    query: { community: homeCommunity.value, published_within_hours: 168, limit: 5 },
  }),
  { watch: [homeCommunity] },
)
const officialRequest = useAsyncData(
  () => `home-official-${homeCommunity.value}-${page.value}`,
  () => api<PublicContentList>('/api/contents', {
    query: {
      community: homeCommunity.value,
      identity: 'official',
      published_within_hours: 168,
      limit: OFFICIAL_PER_PAGE,
      offset: (page.value - 1) * OFFICIAL_PER_PAGE,
    },
  }),
  { watch: [homeCommunity, page] },
)
const communityRequest = useAsyncData(
  () => `home-community-${homeCommunity.value}-${page.value}`,
  () => api<PublicContentList>('/api/contents', {
    query: {
      community: homeCommunity.value,
      exclude_identity: 'official',
      published_within_hours: 168,
      limit: COMMUNITY_PER_PAGE,
      offset: (page.value - 1) * COMMUNITY_PER_PAGE,
    },
  }),
  { watch: [homeCommunity, page] },
)
const highlightsRequest = useAsyncData(
  () => `home-highlights-${homeCommunity.value}`,
  () => api<PublicContentList>('/api/highlights', { query: { community: homeCommunity.value, limit: 5 } }),
  { watch: [homeCommunity] },
)

const [
  { data: eventsData, status: eventsStatus },
  { data: officialData, status: officialStatus },
  { data: communityData, status: communityStatus },
  { data: highlightsData },
] = await Promise.all([
  eventsRequest,
  officialRequest,
  communityRequest,
  highlightsRequest,
])

const featuredHighlights = computed(() => (highlightsData.value?.items ?? []).map(toRawItem))
const eventList = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))

// 最新最热混合流：官方内容视作更新 36h、高播放内容视作更新 12h，
// 页内按"有效时间"排序——同一屏里最新、最热、官方三者兼顾。
function effectiveTs(item: PublicContentItem): number {
  const base = new Date(item.published_at || item.collected_at).getTime()
  let ts = base
  if (item.identity === 'official') ts += OFFICIAL_BOOST_HOURS * 3_600_000
  if ((item.metrics?.view ?? 0) >= 10_000) ts += HOT_BOOST_HOURS * 3_600_000
  return ts
}
const feedItems = computed(() =>
  [...(officialData.value?.items ?? []), ...(communityData.value?.items ?? [])]
    .sort((a, b) => effectiveTs(b) - effectiveTs(a))
    .map(toRawItem),
)
const totalPages = computed(() =>
  Math.max(
    Math.ceil((officialData.value?.total ?? 0) / OFFICIAL_PER_PAGE),
    Math.ceil((communityData.value?.total ?? 0) / COMMUNITY_PER_PAGE),
    1,
  ),
)
const feedTotal = computed(() => (officialData.value?.total ?? 0) + (communityData.value?.total ?? 0))
const loading = computed(() =>
  [eventsStatus, officialStatus, communityStatus].some((s) => s.value === 'pending'),
)
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
      officialRequest.refresh(),
      communityRequest.refresh(),
      highlightsRequest.refresh(),
    ])
  }
  finally {
    refreshing.value = false
  }
}

// 温和的新内容探测：绝不自动改写正在看的列表，只在后台对比当前分区两侧列表头，
// 有差异时亮出「有新内容」提示，由用户决定何时更新。
const pendingNewContent = ref(false)
let updateTimer: ReturnType<typeof setInterval> | undefined

function currentHeads(): [string | null, string | null] {
  const officialHead = officialData.value?.items[0]?.id
  const communityHead = communityData.value?.items[0]?.id
  return [
    officialHead == null ? null : String(officialHead),
    communityHead == null ? null : String(communityHead),
  ]
}

async function probeHead(identity: 'official' | 'community'): Promise<string | null> {
  const res = await api<PublicContentList>('/api/contents', {
    query: {
      community: homeCommunity.value,
      limit: 1,
      published_within_hours: 168,
      ...(identity === 'official' ? { identity } : { exclude_identity: identity }),
    },
  })
  return res.items[0]?.id != null ? String(res.items[0].id) : null
}

async function checkForUpdates() {
  if (document.visibilityState !== 'visible') return
  if (pendingNewContent.value || loading.value || refreshing.value) return
  try {
    const [officialHead, communityHead] = await Promise.all([
      probeHead('official'),
      probeHead('community'),
    ])
    const [currentOfficial, currentCommunity] = currentHeads()
    const changed
      = (officialHead && currentOfficial && officialHead !== currentOfficial)
        || (communityHead && currentCommunity && communityHead !== currentCommunity)
    if (changed) pendingNewContent.value = true
  } catch { /* 探测失败静默跳过 */ }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') void checkForUpdates()
}

async function applyUpdates() {
  pendingNewContent.value = false
  await refreshHome()
}

watch([page, homeCommunity], () => {
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
</script>

<template>
  <div>
    <!-- 栏目导航与刷新同处一行，顶部只有一层吸顶栏 -->
    <div class="mb-4 flex items-center justify-between gap-2">
      <nav class="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto text-sm [scrollbar-width:none]" aria-label="站点栏目">
        <NuxtLink
          v-for="l in sectionLinks"
          :key="l.to"
          :to="{ path: l.to, query: communityQuery }"
          class="whitespace-nowrap rounded-md px-2.5 py-1.5 transition-colors"
          :class="
            isActive(l.to)
              ? 'bg-blue-500/10 font-medium text-blue-600 dark:bg-blue-400/15 dark:text-blue-300'
              : 'text-slate-600 hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100'
          "
        >
          {{ l.label }}
        </NuxtLink>
      </nav>
      <div class="flex shrink-0 items-center gap-2">
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
      </div>
    </div>

    <OfficialLinks variant="strip" />
    <section v-if="loading" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>

    <template v-else>
      <!-- 官方精选轮播：官方内容的第一呈现位 -->
      <section aria-labelledby="official-latest-title" class="mb-6">
        <header class="mb-3 flex items-end justify-between gap-4 px-1">
          <h1 id="official-latest-title" class="text-lg font-semibold text-slate-900 dark:text-zinc-50">官方精选</h1>
          <NuxtLink to="/official" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            全部官方动态
          </NuxtLink>
        </header>
        <FeaturedCarousel v-if="featuredHighlights.length" :items="featuredHighlights" :show-community="aggregate" />
        <div v-else class="panel px-4 py-10 text-center text-sm text-slate-400">
          近 7 天暂无精选内容。
        </div>
      </section>

      <!-- 聚合结论：多条线索归并出的结论，压缩为 5 条速览 -->
      <section aria-labelledby="briefing-title" class="mb-6">
        <header class="mb-3 flex items-end justify-between gap-4 px-1">
          <h2 id="briefing-title" class="text-base font-semibold text-slate-900 dark:text-zinc-50">近期聚合结论</h2>
          <NuxtLink to="/events" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">查看事件库</NuxtLink>
        </header>
        <div class="panel overflow-hidden">
          <EventRow v-for="e in eventList" :key="e.id" :event="e" :show-community="aggregate" />
          <p v-if="!eventList.length" class="px-4 py-8 text-center text-sm text-slate-400">
            近 7 天暂无新事件。
          </p>
        </div>
      </section>

      <!-- 最新最热混合流：官方 + 社区按有效时间混排，官方加权、热门置前 -->
      <section aria-labelledby="feed-title">
        <header class="mb-3 flex items-end justify-between gap-4 px-1">
          <h2 id="feed-title" class="text-base font-semibold text-slate-900 dark:text-zinc-50">最新 · 最热</h2>
          <NuxtLink to="/contents" class="link shrink-0 rounded-md px-1 py-0.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            查看内容库
          </NuxtLink>
        </header>
        <div class="panel overflow-hidden">
          <RawFeedRow v-for="row in feedItems" :key="row.id" :item="row" :show-community="aggregate" />
          <p v-if="!feedItems.length" class="px-4 py-10 text-center text-sm text-slate-400">
            近 7 天暂无内容。
          </p>
          <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
            <span>共 {{ feedTotal }} 条</span>
            <div class="flex items-center gap-3">
              <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
              <span class="mono">{{ page }} / {{ totalPages }}</span>
              <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
            </div>
          </div>
        </div>
      </section>
    </template>

    <div class="mt-6 space-y-4 xl:hidden">
      <StatusCard />
      </div>
  </div>
</template>
