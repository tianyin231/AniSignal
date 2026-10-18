<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import type { ApiEventList, PublicContentList, PublicContentItem, CalendarDto, CodesDto, PublicSource } from '~/composables/usePublicApi'
import { toRawItem, utcTimestamp } from '~/utils/public-utils'
import { ongoingActivities, upNextItems } from '~/utils/schedule'

useSeoMeta({ title: '首页 · AniSignal' })
definePageMeta({ middleware: [(to) => { if (to.query.community === 'ai') return navigateTo('/ai') }] })

const route = useRoute()
const router = useRouter()
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const referenceTime = useState('home-reference-time', () => Date.now())
// "全部"标签下官方内容在混合流中占约 1/3（官方:社区 = 15:30 每页），官方比重由此保证
const OFFICIAL_PER_PAGE = 15
const COMMUNITY_PER_PAGE = 30
const TAB_PER_PAGE = 30
const OFFICIAL_BOOST_HOURS = 36
const HOT_BOOST_HOURS = 12

const FEED_TABS = [
  { key: 'all', label: '全部' },
  { key: 'official', label: '官方' },
  { key: 'community', label: '社区' },
  { key: 'hot', label: '热门' },
] as const
type FeedTab = typeof FEED_TABS[number]['key']
const feedTab = computed<FeedTab>(() =>
  FEED_TABS.some(t => t.key === route.query.feed) ? route.query.feed as FeedTab : 'all',
)

const { community } = useGameSection()
const { api } = usePublicApi()

// 首页频道语义：/ 无 community 参数 = 跨分区聚合（community=all）；带参数 = 该分区首页
const aggregate = computed(() => !route.query.community || route.query.community === 'all')
const homeCommunity = computed(() => (aggregate.value ? 'all' : community.value))
const sectionQuery = computed(() => ({ community: homeCommunity.value }))

// 最新·最热混合流：官方内容视作更新 36h、高播放内容视作更新 12h，
// 页内按"有效时间"排序——同一屏里最新、最热、官方三者兼顾。
function effectiveTs(item: PublicContentItem): number {
  let ts = utcTimestamp(item.published_at || item.collected_at)
  if (item.identity === 'official') ts += OFFICIAL_BOOST_HOURS * 3_600_000
  if ((item.metrics?.view ?? 0) >= 10_000) ts += HOT_BOOST_HOURS * 3_600_000
  return ts
}

interface FeedResult {
  items: PublicContentItem[]
  total: number
  pages: number
  /** 官方 / 社区两侧列表头（仅"全部"标签），用于新内容探测 */
  heads: [string | null, string | null]
}
const headId = (list: PublicContentList) => (list.items[0]?.id != null ? String(list.items[0].id) : null)

// 客户端先切换频道并显示加载状态；服务端首屏仍等待数据。
const feedRequest = useLazyAsyncData(
  () => `home-feed-${homeCommunity.value}-${feedTab.value}-${page.value}`,
  async (_app, { signal }): Promise<FeedResult> => {
    const base = { community: homeCommunity.value, published_within_hours: 168 }
    if (feedTab.value === 'all') {
      const [official, rest] = await Promise.all([
        api<PublicContentList>('/api/contents', { signal, query: { ...base, identity: 'official', limit: OFFICIAL_PER_PAGE, offset: (page.value - 1) * OFFICIAL_PER_PAGE } }),
        api<PublicContentList>('/api/contents', { signal, query: { ...base, exclude_identity: 'official', limit: COMMUNITY_PER_PAGE, offset: (page.value - 1) * COMMUNITY_PER_PAGE } }),
      ])
      return {
        items: [...official.items, ...rest.items].sort((a, b) => effectiveTs(b) - effectiveTs(a)),
        total: official.total + rest.total,
        pages: Math.max(Math.ceil(official.total / OFFICIAL_PER_PAGE), Math.ceil(rest.total / COMMUNITY_PER_PAGE), 1),
        heads: [headId(official), headId(rest)],
      }
    }
    const filter = feedTab.value === 'official'
      ? { identity: 'official' }
      : feedTab.value === 'community' ? { exclude_identity: 'official' } : { sort: 'hot' }
    const res = await api<PublicContentList>('/api/contents', {
      signal,
      query: { ...base, ...filter, limit: TAB_PER_PAGE, offset: (page.value - 1) * TAB_PER_PAGE },
    })
    return { items: res.items, total: res.total, pages: Math.max(1, Math.ceil(res.total / TAB_PER_PAGE)), heads: [null, null] }
  },
  { watch: [homeCommunity, page, feedTab] },
)
const eventsRequest = useLazyAsyncData(
  () => `home-events-${homeCommunity.value}`,
  (_app, { signal }) => api<ApiEventList>('/api/events', { signal, query: { community: homeCommunity.value, limit: 6 } }),
  { watch: [homeCommunity] },
)
const highlightsRequest = useLazyAsyncData(
  () => `home-highlights-${homeCommunity.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/highlights', { query: { community: homeCommunity.value, limit: 5 }, signal }),
  { watch: [homeCommunity] },
)
const trendingRequest = useLazyAsyncData(
  () => `home-trending-${homeCommunity.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/trending', { query: { community: homeCommunity.value, hours: 24, limit: 5 }, signal }),
  { watch: [homeCommunity] },
)
const calendarRequest = useLazyAsyncData(
  () => `home-calendar-${homeCommunity.value}`,
  (_app, { signal }) => api<CalendarDto>('/api/calendar', { query: { community: homeCommunity.value }, signal }),
  { watch: [homeCommunity] },
)
const codesRequest = useLazyAsyncData(
  () => `home-codes-${homeCommunity.value}`,
  (_app, { signal }) => api<CodesDto>('/api/codes', { query: { community: homeCommunity.value }, signal }),
  { watch: [homeCommunity] },
)
// 页头数字：近 24 小时新内容数只取 total
const pulseRequest = useLazyAsyncData(
  () => `home-pulse-${homeCommunity.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/contents', { query: { community: homeCommunity.value, published_within_hours: 24, limit: 1 }, signal }),
  { watch: [homeCommunity] },
)
const sourcesRequest = useLazyAsyncData(
  () => `home-sources-${homeCommunity.value}`,
  (_app, { signal }) => api<{ items: PublicSource[] }>('/api/sources', { query: { community: homeCommunity.value }, signal }),
  { watch: [homeCommunity] },
)

const requests = [feedRequest, eventsRequest, highlightsRequest, trendingRequest, calendarRequest, codesRequest, pulseRequest, sourcesRequest]
// 频道入口与其他首屏数据并行预取，客户端挂载时直接复用缓存，避免头图二次展开。
const { fetchOnce: fetchOfficialLinks } = useOfficialLinks(homeCommunity)
const officialLinksRequest = import.meta.server && !aggregate.value ? fetchOfficialLinks() : Promise.resolve()
// 服务端首屏等全部数据；客户端切换频道时先渲染结构、各区块随数据到达再填充
await Promise.all([...requests, officialLinksRequest])
const { data: feedData, status: feedStatus } = feedRequest
const { data: eventsData } = eventsRequest
const { data: highlightsData } = highlightsRequest
const { data: trendingData } = trendingRequest
const { data: calendarData } = calendarRequest
const { data: codesData } = codesRequest
const { data: pulseData } = pulseRequest
const { data: sourcesData } = sourcesRequest

const featuredHighlights = computed(() => (highlightsData.value?.items ?? []).map(toRawItem))
const trendingList = computed(() => (trendingData.value?.items ?? []).map(toRawItem))
const trendingDelta = computed(() =>
  new Map((trendingData.value?.items ?? []).map(item => [String(item.id), item.delta_view ?? 0])),
)
const feedItems = computed(() => (feedData.value?.items ?? []).map(toRawItem))
const totalPages = computed(() => feedData.value?.pages ?? 1)
const feedTotal = computed(() => feedData.value?.total ?? 0)

// 页头 / 频道头图数字
const newCount = computed(() => pulseData.value?.total ?? null)
const ongoingCount = computed(() => ongoingActivities(calendarData.value?.ongoing ?? [], referenceTime.value).length)
const upcomingCount = computed(() => upNextItems(calendarData.value, codesData.value?.items, referenceTime.value, 99).length)
const enabledSources = computed(() => (sourcesData.value?.items ?? []).filter(s => s.enabled))
const sourcesOk = computed(() => enabledSources.value.filter(s => s.consecutive_failures === 0).length)
// 频道头图用精选里的另一张图，避免与下方精选大图首帧重复
const heroCover = computed(() => {
  const items = featuredHighlights.value
  const rest = items.slice(1)
  return (rest.find(i => i.identity === 'official' && i.cover) ?? rest.find(i => i.cover) ?? items.find(i => i.cover))?.cover
})
const latestEvent = computed(() => {
  const event = eventsData.value?.items[0]
  return event ? { title: event.title, to: `/events/${event.id}` } : null
})

const refreshing = ref(false)
const homeError = computed(() => requests.some(request => request.error.value))

const feedSection = ref<HTMLElement | null>(null)
async function goPage(p: number) {
  await router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
  // 翻页按钮在列表底部：换页后回到内容流顶部，而不是停在新一页的末尾
  feedSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function selectTab(key: FeedTab) {
  router.replace({ query: { ...route.query, feed: key === 'all' ? undefined : key, page: undefined } })
}

async function refreshHome() {
  if (refreshing.value) return
  refreshing.value = true
  referenceTime.value = Date.now()
  try {
    await Promise.all([
      ...requests.map(request => request.refresh()),
      refreshNuxtData(`status-product-${homeCommunity.value}`),
    ])
  }
  finally {
    refreshing.value = false
  }
}

// 温和的新内容探测：绝不自动改写正在看的列表，只在后台对比"全部"流两侧列表头，
// 有差异时亮出「有新内容」提示，由用户决定何时更新。
const pendingNewContent = ref(false)
let updateTimer: ReturnType<typeof setInterval> | undefined
let probeController: AbortController | undefined

async function probeHead(identity: 'official' | 'community', scope: string, signal: AbortSignal): Promise<string | null> {
  const res = await api<PublicContentList>('/api/contents', {
    signal,
    query: {
      community: scope,
      limit: 1,
      published_within_hours: 168,
      ...(identity === 'official' ? { identity } : { exclude_identity: 'official' }),
    },
  })
  return headId(res)
}

async function checkForUpdates() {
  if (document.visibilityState !== 'visible') return
  if (feedTab.value !== 'all' || page.value !== 1 || pendingNewContent.value) return
  if (feedStatus.value === 'pending' || refreshing.value || homeError.value || probeController) return
  const scope = homeCommunity.value
  const controller = new AbortController()
  probeController = controller
  try {
    const [officialHead, communityHead] = await Promise.all([
      probeHead('official', scope, controller.signal),
      probeHead('community', scope, controller.signal),
    ])
    if (controller.signal.aborted || scope !== homeCommunity.value || page.value !== 1 || feedTab.value !== 'all') return
    const [currentOfficial, currentCommunity] = feedData.value?.heads ?? [null, null]
    const changed
      = (officialHead && officialHead !== currentOfficial)
        || (communityHead && communityHead !== currentCommunity)
    if (changed) pendingNewContent.value = true
  } catch { /* 探测失败静默跳过 */ }
  finally { if (probeController === controller) probeController = undefined }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') {
    referenceTime.value = Date.now()
    void checkForUpdates()
  }
}

async function applyUpdates() {
  pendingNewContent.value = false
  await refreshHome()
}

watch([page, homeCommunity, feedTab], () => {
  probeController?.abort()
  pendingNewContent.value = false
})

onMounted(() => {
  updateTimer = setInterval(checkForUpdates, 60_000)
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => {
  probeController?.abort()
  if (updateTimer) clearInterval(updateTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div>
    <ChannelHero
      v-if="!aggregate"
      :community-key="homeCommunity"
      :cover="heroCover"
      :new-count="newCount"
      :ongoing="ongoingCount"
      :upcoming="upcomingCount"
      :latest="latestEvent"
    />
    <HomeBriefing
      v-else
      :now="referenceTime"
      :new-count="newCount"
      :ongoing="ongoingCount"
      :sources-ok="sourcesOk"
      :sources-total="enabledSources.length"
    />

    <SectionNav>
      <button
        v-if="pendingNewContent"
        type="button"
        class="inline-flex h-9 items-center gap-1.5 whitespace-nowrap rounded-full bg-brand-soft px-3 text-xs font-semibold text-brand-ink transition-colors hover:bg-brand/20"
        @click="applyUpdates"
      >
        <span class="size-1.5 animate-pulse rounded-full bg-brand" aria-hidden="true" />
        有新内容
      </button>
      <button
        type="button"
        :disabled="refreshing"
        class="inline-flex h-9 items-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] text-muted transition-colors hover:bg-chip hover:text-ink disabled:cursor-wait"
        @click="pendingNewContent ? applyUpdates() : refreshHome()"
      >
        <RefreshCw :size="14" :class="{ 'animate-spin': refreshing }" aria-hidden="true" />
        {{ refreshing ? '刷新中…' : '刷新' }}
      </button>
    </SectionNav>

    <section v-if="homeError" role="alert" class="panel mb-5 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
      部分内容暂时加载失败。<button type="button" class="ml-1 underline" :disabled="refreshing" @click="refreshHome">重新加载</button>
    </section>

    <div class="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <FeaturedHero v-if="featuredHighlights.length" :items="featuredHighlights" :show-community="aggregate" />
      <div v-else class="panel grid min-h-[340px] place-items-center text-sm text-muted">近 7 天暂无精选内容。</div>
      <div class="flex min-w-0 flex-col gap-5">
        <UpNextCard :calendar="calendarData" :codes="codesData" :now="referenceTime" :calendar-scope="homeCommunity" />
        <StatusCard embedded />
      </div>
    </div>

    <ActivityStrip
      :entries="calendarData?.ongoing ?? []"
      :now="referenceTime"
      :calendar-scope="homeCommunity"
      :show-community="aggregate"
    />

    <div class="mt-10 grid items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <section ref="feedSection" class="panel min-w-0 scroll-mt-28 overflow-hidden md:scroll-mt-20" aria-labelledby="feed-title">
        <header class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3.5 sm:px-5">
          <h2 id="feed-title" class="text-lg font-bold text-ink">最新 · 最热</h2>
          <div role="tablist" aria-label="内容筛选" class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]">
            <button
              v-for="tab in FEED_TABS"
              :key="tab.key"
              type="button"
              role="tab"
              :aria-selected="feedTab === tab.key"
              class="h-8 rounded-lg px-3.5 text-[13px] transition-colors"
              :class="feedTab === tab.key ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
              @click="selectTab(tab.key)"
            >
              {{ tab.label }}
            </button>
          </div>
        </header>
        <p v-if="feedStatus === 'pending' && !feedItems.length" class="px-4 py-16 text-center text-sm text-muted">加载中…</p>
        <div v-else :class="{ 'opacity-60 transition-opacity': feedStatus === 'pending' }">
          <RawFeedRow v-for="row in feedItems" :key="row.id" :item="row" :show-community="aggregate" />
          <p v-if="!feedItems.length" class="px-4 py-16 text-center text-sm text-muted">近 7 天暂无内容。</p>
        </div>
        <footer class="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-xs text-muted sm:px-5">
          <span>近 7 天共 <span class="num text-[13px] font-semibold text-ink-2">{{ feedTotal }}</span> 条</span>
          <div v-if="totalPages > 1" class="flex items-center gap-1">
            <button :disabled="page <= 1" class="h-9 rounded-[10px] px-3 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40" @click="goPage(page - 1)">上一页</button>
            <span class="mono px-1.5">{{ page }} / {{ totalPages }}</span>
            <button :disabled="page >= totalPages" class="h-9 rounded-[10px] px-3 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40" @click="goPage(page + 1)">下一页</button>
          </div>
        </footer>
      </section>

      <aside class="flex min-w-0 flex-col gap-5" aria-label="首页侧栏">
        <TrendingCard :items="trendingList" :deltas="trendingDelta" :more-to="{ path: '/contents', query: { ...sectionQuery, tab: 'trending' } }" />
        <EventsCard :events="eventsData?.items ?? []" :show-community="aggregate" :more-to="{ path: '/events', query: sectionQuery }" />
        <CodesCard :codes="codesData?.items ?? []" :show-community="aggregate" />
        <SourceHealthCard :sources="sourcesData?.items ?? []" />
        <SubscribeCta />
      </aside>
    </div>
  </div>
</template>
