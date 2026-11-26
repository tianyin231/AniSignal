<script setup lang="ts">
import { Flame, Hash, LayoutGrid, List, Radio, Search, SearchX } from 'lucide-vue-next'
import type { FeedOverview, PublicContentList } from '~/composables/usePublicApi'
import { toRawItem } from '~/utils/public-utils'
import { communityMeta, platformsForCommunity, useCommunityScope } from '~/composables/useGameSection'
import { platformMeta, type Platform } from '~/utils/data'
import { fmtTime } from '~/utils/admin-types'
import { cleanPlatformTitle } from '~/utils/text'
import { formatCompactStars, type TrendingEditionsResponse, type TrendingPeriod } from '~/utils/git-trending'

const route = useRoute()
const router = useRouter()
const pageSize = 30
// 后端公开列表的分页上界是 10000（MAX_PUBLIC_OFFSET），折算成页数后前后端一致
const MAX_PAGE = Math.floor(10_000 / pageSize) + 1
const platform = computed(() => String(route.query.platform || ''))
const q = computed(() => String(route.query.q || ''))
const activeTopic = computed(() => String(route.query.topic || ''))
const { scope, isAll } = useCommunityScope()
const isXiaoheihe = computed(() => scope.value === 'xiaoheihe')
const page = computed(() => Math.min(MAX_PAGE, Math.max(1, Number(route.query.page) || 1)))
const topic = computed(() => {
  const meta = communityMeta(scope.value)
  return meta?.domain === 'feed' ? meta : undefined
})
const sort = computed(() => String(route.query.sort || 'latest'))
const tab = computed(() => (topic.value ? 'latest' : String(route.query.tab || 'latest')))
const trendingPeriod = computed(() => {
  const raw = String(route.query.period || '')
  return (['daily', 'weekly', 'monthly'].includes(raw) ? raw : '') as TrendingPeriod | ''
})
const trendingIssue = computed(() => String(route.query.issue || ''))
const layoutMode = ref<'card' | 'list'>('card')
const searchInput = ref(q.value)
watch(q, (v) => (searchInput.value = v))

useSeoMeta({
  title: () => `${topic.value?.label ?? '内容库'} · AniSignal`,
  description: () => topic.value ? `${topic.value.label}主题流动态、AI 解读与持续归档。` : '多平台采集的官方与社区内容，可按分区、平台和时间筛选。',
})

const { api } = usePublicApi()
const shouldCheckGitTrending = computed(() => Boolean(topic.value && scope.value !== 'xiaoheihe'))
const shouldLoadOverview = computed(() => Boolean(topic.value))

const overviewRequest = useLazyAsyncData(
  () => `feed-overview-${scope.value}`,
  (_app, { signal }) => api<FeedOverview>('/api/feeds/overview', {
    signal,
    query: { community: scope.value },
  }),
  { enabled: shouldLoadOverview, watch: [scope] },
)
const gitTrendingRequest = useLazyAsyncData(
  () => `git-trending-${scope.value}-${trendingPeriod.value || 'auto'}-${trendingIssue.value || 'latest'}`,
  (_app, { signal }) => api<TrendingEditionsResponse>('/api/git-trending/editions', {
    signal,
    query: {
      community: scope.value,
      ...(trendingPeriod.value ? { period: trendingPeriod.value } : {}),
      ...(trendingIssue.value ? { period_key: trendingIssue.value } : {}),
    },
  }),
  { enabled: shouldCheckGitTrending, watch: [scope, trendingPeriod, trendingIssue] },
)
const contentsRequest = useLazyAsyncData(
  () => `contents-${scope.value}-${platform.value}-${q.value}-${activeTopic.value}-${page.value}-${tab.value}-${sort.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/contents', {
    signal,
    query: {
      community: scope.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      ...(sort.value === 'hot' ? { sort: 'hot' } : {}),
      ...(platform.value ? { platform: platform.value } : {}),
      ...(q.value ? { q: q.value } : {}),
      ...(activeTopic.value ? { topic: activeTopic.value } : {}),
    },
  }),
  { enabled: computed(() => tab.value !== 'trending'), watch: [scope, platform, q, activeTopic, page, tab, sort] },
)
const trendingRequest = useLazyAsyncData(
  () => `trending-${scope.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/trending', { query: { community: scope.value, hours: 24, limit: 20 }, signal }),
  { enabled: computed(() => tab.value === 'trending'), watch: [scope, tab] },
)

const [
  { data, status, error },
  { data: trendingData, status: trendingStatus, error: trendingError },
  { data: rawGitTrendingData, status: gitTrendingStatus },
  { data: rawOverviewData },
] = await Promise.all([
  contentsRequest,
  trendingRequest,
  gitTrendingRequest,
  overviewRequest,
])

// 按当前 scope 严格隔离，避免客户端在「开源 ↔ 小黑盒」间切换时短暂残留上一个分区的头图/侧栏数据
const overviewData = computed(() => (rawOverviewData.value?.community === scope.value ? rawOverviewData.value : null))
const gitTrendingData = computed(() => (rawGitTrendingData.value?.community === scope.value ? rawGitTrendingData.value : null))

const hasGitEditions = computed(() => Boolean(shouldCheckGitTrending.value && gitTrendingData.value?.has_editions))
const showGitDesk = computed(() => {
  if (!hasGitEditions.value) return false
  if (route.query.view === 'feed') return false
  if (q.value || platform.value || activeTopic.value || sort.value === 'hot') return false
  return true
})
const retry = () => (tab.value === 'trending' ? trendingRequest.refresh() : contentsRequest.refresh())

const list = computed(() =>
  tab.value === 'trending'
    ? (trendingData.value?.items ?? []).map(toRawItem)
    : (data.value?.items ?? []).map(toRawItem),
)
const trendingDelta = computed(() =>
  new Map((trendingData.value?.items ?? []).map(item => [String(item.id), item.delta_view ?? 0])),
)
const totalPages = computed(() => Math.min(MAX_PAGE, Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize))))

const visiblePlatformTabs = computed(() =>
  platformsForCommunity(scope.value).filter(key => key in platformMeta),
)
const overviewTopics = computed(() => overviewData.value?.topics ?? [])
const maxTopicCount = computed(() => Math.max(1, ...(overviewTopics.value.map(t => t.count))))

watch(scope, () => {
  if (platform.value && !visiblePlatformTabs.value.includes(platform.value)) {
    applyQuery({ platform: undefined })
  }
})

function applyQuery(patch: Record<string, string | undefined>) {
  router.replace({ query: { ...route.query, page: undefined, ...patch } })
}

function switchFeedView(view: 'editions' | 'feed') {
  if (view === 'editions') {
    applyQuery({ view: undefined, q: undefined, platform: undefined, topic: undefined, sort: undefined })
  } else {
    applyQuery({ view: 'feed' })
  }
}

function selectTrendingPeriod(period: TrendingPeriod) {
  applyQuery({ view: undefined, period, issue: undefined })
}

function selectTrendingIssue(periodKey: string) {
  applyQuery({ view: undefined, period: gitTrendingData.value?.active_period || undefined, issue: periodKey })
}

function toggleTopic(tag: string) {
  const next = activeTopic.value === tag ? undefined : tag
  applyQuery({ ...(hasGitEditions.value ? { view: 'feed' } : {}), topic: next })
}

function clearTopicFilters() {
  searchInput.value = ''
  applyQuery({ q: undefined, platform: undefined, topic: undefined, sort: undefined })
}

function formatHotMetric(item: FeedOverview['hot_items'][number]): string {
  const m = item.metrics
  if (!m) return ''
  if (item.content_type === 'repo') {
    if (m.view) return `+${Number(m.view).toLocaleString('zh-CN')} 新星`
    if (m.like) return `★ ${formatCompactStars(Number(m.like))}`
    return ''
  }
  if (m.like != null && m.like > 0) return `${Number(m.like).toLocaleString('zh-CN')} 赞`
  if (m.reply != null && m.reply > 0) return `${Number(m.reply).toLocaleString('zh-CN')} 评`
  if (m.view != null && m.view > 0) return `${Number(m.view).toLocaleString('zh-CN')} 阅`
  return ''
}

const customCreateTo = computed(() =>
  isXiaoheihe.value ? '/account?tab=custom&create=1&source=xiaoheihe' : '/account?tab=custom&create=1',
)
</script>

<template>
  <div class="space-y-5">
    <!-- 主题流信号台频道头图（小黑盒 / 开源 / 所有自定义 domain="feed" 分区） -->
    <TopicChannelHero
      v-if="topic"
      :topic="topic"
      :overview="overviewData"
      :git-trending="gitTrendingData"
      :has-git-editions="hasGitEditions"
      :show-git-desk="showGitDesk"
      :custom-create-to="customCreateTo"
      @switch-view="switchFeedView"
    />

    <!-- 游戏分区内容库导航 -->
    <template v-else>
      <h1 class="sr-only">内容库</h1>
      <SectionNav />
    </template>

    <!-- 专属趋势刊物台（当该主题流拥有 GitHub Trending 归档且处于刊物视图时） -->
    <GitTrendingDesk
      v-if="showGitDesk && gitTrendingData"
      :data="gitTrendingData"
      :loading="gitTrendingStatus === 'pending'"
      @select-period="selectTrendingPeriod"
      @select-issue="selectTrendingIssue"
    />

    <!-- 升级后的双栏信号台主题流（小黑盒 / 开源全部收录 / 所有自定义 feed 分区） -->
    <div v-else-if="topic" class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
      <!-- 左主栏：筛选控制条 + 卡片/列表内容流 -->
      <div class="min-w-0 space-y-4">
        <section class="panel p-3.5 sm:p-4">
          <div class="flex flex-col gap-2.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            <form class="flex min-w-0 w-full items-center gap-2 sm:w-auto sm:flex-initial" role="search" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
              <div class="relative min-w-0 flex-1 sm:flex-initial">
                <Search :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
                <input v-model="searchInput" type="search" aria-label="关键词搜索" placeholder="搜索标题、正文或项目名" class="input h-9 w-full pl-9 text-[13px] sm:w-64">
              </div>
              <button type="submit" class="btn-primary h-9 shrink-0 px-3.5 text-xs">搜索</button>
            </form>

            <div class="flex items-center justify-between gap-2 sm:justify-end">
              <div class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="排序">
                <button
                  type="button"
                  role="tab"
                  :aria-selected="sort !== 'hot'"
                  class="h-8 rounded-lg px-3 text-[13px] transition-colors"
                  :class="sort !== 'hot' ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
                  @click="applyQuery({ sort: undefined })"
                >
                  最新
                </button>
                <button
                  type="button"
                  role="tab"
                  :aria-selected="sort === 'hot'"
                  class="inline-flex h-8 items-center gap-1 rounded-lg px-3 text-[13px] transition-colors"
                  :class="sort === 'hot' ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
                  @click="applyQuery({ sort: 'hot' })"
                >
                  <Flame :size="14" class="text-hot" aria-hidden="true" />热门
                </button>
              </div>

              <!-- 卡片 / 紧凑列表双视图切换 -->
              <div class="inline-flex rounded-[10px] bg-chip p-[3px]" role="group" aria-label="浏览密度">
                <button
                  type="button"
                  class="grid size-8 place-items-center rounded-lg transition-colors"
                  :class="layoutMode === 'card' ? 'bg-card text-ink shadow-sm' : 'text-muted hover:text-ink'"
                  :aria-pressed="layoutMode === 'card'"
                  aria-label="卡片视图"
                  title="图文卡片视图"
                  @click="layoutMode = 'card'"
                >
                  <LayoutGrid :size="15" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="grid size-8 place-items-center rounded-lg transition-colors"
                  :class="layoutMode === 'list' ? 'bg-card text-ink shadow-sm' : 'text-muted hover:text-ink'"
                  :aria-pressed="layoutMode === 'list'"
                  aria-label="紧凑列表"
                  title="紧凑列表视图"
                  @click="layoutMode = 'list'"
                >
                  <List :size="15" aria-hidden="true" />
                </button>
              </div>
            </div>
          </div>

          <!-- 平台筛选（多平台分区显示） -->
          <div v-if="visiblePlatformTabs.length > 1" class="mt-3 flex flex-wrap gap-1.5 border-t border-line-soft pt-3" aria-label="平台筛选">
            <button
              type="button"
              class="h-7 rounded-full px-3 text-xs transition-colors"
              :class="!platform ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
              :aria-pressed="!platform"
              @click="applyQuery({ platform: undefined })"
            >
              全部平台
            </button>
            <button
              v-for="key in visiblePlatformTabs"
              :key="key"
              type="button"
              class="h-7 rounded-full px-3 text-xs transition-colors"
              :class="platform === key ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
              :aria-pressed="platform === key"
              @click="applyQuery({ platform: key })"
            >
              {{ platformMeta[key as Platform]?.label }}
            </button>
          </div>

          <!-- 话题 / AI 标签筛选胶囊（自动聚合小黑盒话题或 AI 主题标签） -->
          <div v-if="overviewTopics.length > 1" class="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line-soft pt-3" aria-label="话题筛选">
            <button
              type="button"
              class="h-7 rounded-full px-3 text-xs transition-colors"
              :class="!activeTopic ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
              :aria-pressed="!activeTopic"
              @click="applyQuery({ topic: undefined })"
            >
              全部话题
            </button>
            <button
              v-for="t in overviewTopics.slice(0, 10)"
              :key="t.name"
              type="button"
              class="inline-flex h-7 items-center gap-1 rounded-full px-3 text-xs transition-colors"
              :class="activeTopic === t.name ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
              :aria-pressed="activeTopic === t.name"
              @click="toggleTopic(t.name)"
            >
              <span>{{ t.name }}</span>
              <span class="num text-[11px] opacity-70">{{ t.count }}</span>
            </button>
          </div>
        </section>

        <!-- 加载 / 错误 / 内容流 -->
        <section v-if="status === 'pending' && !list.length" class="panel px-4 py-12 text-center text-sm text-muted">
          加载中…
        </section>
        <section v-else-if="error" role="alert" class="panel px-4 py-12 text-center text-sm text-warn">
          内容暂时加载失败。<button type="button" class="ml-1 font-medium underline" @click="retry">重新加载</button>
        </section>
        <template v-else-if="list.length">
          <!-- 图文卡片视图（默认） -->
          <div v-if="layoutMode === 'card'" class="space-y-3" :class="{ 'opacity-65 transition-opacity': status === 'pending' }">
            <TopicFeedCard
              v-for="item in list"
              :key="item.id"
              :item="item"
              :active-topic="activeTopic"
              @select-topic="toggleTopic"
            />
          </div>

          <!-- 紧凑列表视图 -->
          <section v-else class="panel overflow-hidden" :class="{ 'opacity-65 transition-opacity': status === 'pending' }">
            <RawFeedRow
              v-for="item in list"
              :key="item.id"
              :item="item"
              :show-community="false"
            />
          </section>

          <!-- 分页 -->
          <div v-if="totalPages > 1" class="panel flex items-center justify-between px-4 py-2.5 text-xs text-muted">
            <span class="num">共 {{ data?.total ?? 0 }} 条</span>
            <div class="flex items-center gap-3">
              <button type="button" :disabled="page <= 1" class="transition-colors hover:text-ink disabled:opacity-40" @click="applyQuery({ page: String(page - 1) })">« 上一页</button>
              <span class="mono tabular-nums">{{ page }} / {{ totalPages }}</span>
              <button type="button" :disabled="page >= totalPages" class="transition-colors hover:text-ink disabled:opacity-40" @click="applyQuery({ page: String(page + 1) })">下一页 »</button>
            </div>
          </div>
        </template>
        <section v-else class="panel px-5 py-12 text-center text-sm text-muted">
          <SearchX :size="24" class="mx-auto mb-2 text-faint" aria-hidden="true" />
          <p>{{ q || activeTopic || platform ? '当前筛选条件下暂无匹配内容' : isXiaoheihe ? '暂无小黑盒帖子。' : '暂无内容。' }}</p>
          <button v-if="q || activeTopic || platform" type="button" class="mt-2 text-xs font-medium text-brand-ink hover:underline" @click="clearTopicFilters">
            清除筛选
          </button>
        </section>
      </div>

      <!-- 右侧信号栏：近期热门 + 热门话题分布 + 收录来源状态 -->
      <aside class="space-y-4 lg:sticky lg:top-[88px]">
        <!-- 近期热门 -->
        <section v-if="overviewData?.hot_items?.length" class="panel p-4">
          <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
            <Flame :size="14" class="text-hot" aria-hidden="true" />
            近期热门
          </h2>
          <ol class="mt-3 divide-y divide-line-soft">
            <li v-for="(hot, idx) in overviewData.hot_items" :key="hot.id" class="py-2.5 first:pt-0 last:pb-0">
              <NuxtLink :to="`/contents/${hot.id}`" class="group flex items-start gap-2.5">
                <span class="num w-4 shrink-0 pt-0.5 text-xs font-bold tabular-nums" :class="idx < 3 ? 'text-accent-ink' : 'text-faint'">
                  {{ idx + 1 }}
                </span>
                <div class="min-w-0 flex-1">
                  <p class="line-clamp-2 text-[13px] font-medium leading-snug text-ink transition-colors group-hover:text-brand-ink">
                    {{ hot.brief?.headline || cleanPlatformTitle(hot.title) }}
                  </p>
                  <div class="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-faint">
                    <span v-if="hot.source_name" class="max-w-[9rem] truncate">{{ hot.source_name }}</span>
                    <span v-if="formatHotMetric(hot)" class="num font-semibold text-muted">{{ formatHotMetric(hot) }}</span>
                  </div>
                </div>
              </NuxtLink>
            </li>
          </ol>
        </section>

        <!-- 热门话题分布 -->
        <section v-if="overviewTopics.length" class="panel p-4">
          <div class="flex items-center justify-between">
            <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
              <Hash :size="14" class="text-accent-ink" aria-hidden="true" />
              热门话题
            </h2>
            <button v-if="activeTopic" type="button" class="text-[11px] text-brand-ink hover:underline" @click="applyQuery({ topic: undefined })">
              重置
            </button>
          </div>
          <ul class="mt-3 space-y-2.5">
            <li v-for="t in overviewTopics.slice(0, 8)" :key="t.name">
              <button type="button" class="group w-full text-left" @click="toggleTopic(t.name)">
                <div class="flex items-center justify-between gap-2 text-xs">
                  <span class="truncate font-medium" :class="activeTopic === t.name ? 'text-brand-ink' : 'text-ink-2 group-hover:text-ink'">
                    {{ t.name }}
                  </span>
                  <span class="num shrink-0 tabular-nums text-muted">{{ t.count }}</span>
                </div>
                <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-chip">
                  <div
                    class="h-full rounded-full transition-all"
                    :class="activeTopic === t.name ? 'bg-brand' : 'bg-accent/70'"
                    :style="{ width: `${Math.max(10, Math.round((t.count / maxTopicCount) * 100))}%` }"
                  />
                </div>
              </button>
            </li>
          </ul>
        </section>

        <!-- 收录来源状态 -->
        <section v-if="overviewData?.sources?.length" class="panel p-4">
          <div class="flex items-center justify-between">
            <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
              <Radio :size="14" class="text-ok" aria-hidden="true" />
              收录来源
            </h2>
            <span class="num text-xs text-faint">{{ overviewData.sources.length }} 源</span>
          </div>
          <ul class="mt-3 space-y-2">
            <li v-for="src in overviewData.sources" :key="`${src.platform}-${src.name}`" class="flex items-center justify-between gap-2 text-xs">
              <span class="inline-flex min-w-0 items-center gap-2 text-ink-2">
                <span
                  class="size-2 shrink-0 rounded-full"
                  :class="src.consecutive_failures === 0 ? 'bg-ok' : 'bg-warn'"
                  aria-hidden="true"
                />
                <span class="truncate">{{ src.name }}</span>
              </span>
              <span class="mono shrink-0 text-[11px] text-faint">
                {{ src.last_success_at ? fmtTime(src.last_success_at).slice(5, 16) : '待同步' }}
              </span>
            </li>
          </ul>
        </section>
      </aside>
    </div>

    <!-- 游戏分区内容库视图（保持与原有游戏分区一致） -->
    <template v-else>
      <section class="panel p-3.5 sm:p-4">
        <div class="flex flex-wrap items-center justify-between gap-2.5">
          <form v-if="tab !== 'trending'" class="flex min-w-0 flex-1 items-center gap-2 sm:flex-initial" role="search" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
            <div class="relative min-w-0 flex-1 sm:flex-initial">
              <Search :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
              <input v-model="searchInput" type="search" aria-label="标题关键词" placeholder="搜索标题关键词" class="input h-9 w-full pl-9 text-[13px] sm:w-64">
            </div>
            <button type="submit" class="btn-primary h-9 shrink-0 px-3.5 text-xs">搜索</button>
          </form>

          <div class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="排序">
            <button
              type="button"
              role="tab"
              :aria-selected="tab !== 'trending'"
              class="h-8 rounded-lg px-3.5 text-[13px] transition-colors"
              :class="tab !== 'trending' ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
              @click="applyQuery({ tab: undefined })"
            >
              最新
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="tab === 'trending'"
              class="inline-flex h-8 items-center gap-1 rounded-lg px-3.5 text-[13px] transition-colors"
              :class="tab === 'trending' ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
              @click="applyQuery({ tab: 'trending' })"
            >
              <Flame :size="14" class="text-hot" aria-hidden="true" />热度飙升
            </button>
          </div>
        </div>

        <div v-if="visiblePlatformTabs.length > 1 && tab !== 'trending'" class="mt-3 flex flex-wrap gap-1.5 border-t border-line-soft pt-3" aria-label="平台筛选">
          <button
            type="button"
            class="h-7 rounded-full px-3 text-xs transition-colors"
            :class="!platform ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
            :aria-pressed="!platform"
            @click="applyQuery({ platform: undefined })"
          >
            全部平台
          </button>
          <button
            v-for="key in visiblePlatformTabs"
            :key="key"
            type="button"
            class="h-7 rounded-full px-3 text-xs transition-colors"
            :class="platform === key ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
            :aria-pressed="platform === key"
            @click="applyQuery({ platform: key })"
          >
            {{ platformMeta[key as Platform]?.label }}
          </button>
        </div>
      </section>

      <section v-if="(tab === 'trending' ? trendingStatus : status) === 'pending'" class="panel px-4 py-10 text-center text-sm text-muted">加载中…</section>
      <section v-else-if="tab === 'trending' ? trendingError : error" role="alert" class="panel px-4 py-10 text-center text-sm text-warn">
        内容暂时加载失败。<button type="button" class="ml-1 font-medium underline" @click="retry">重新加载</button>
      </section>
      <section v-else class="panel overflow-hidden">
        <RawFeedRow
          v-for="item in list"
          :key="item.id"
          :item="item"
          :show-community="isAll"
          :delta-view="tab === 'trending' ? trendingDelta.get(item.id) ?? 0 : 0"
        />
        <p v-if="!list.length" class="px-4 py-12 text-center text-sm text-muted">
          {{ tab === 'trending' ? '24 小时内还没有明显的热度变化。' : '没有匹配的内容。' }}
        </p>
        <div v-if="tab !== 'trending' && totalPages > 1" class="flex items-center justify-between border-t border-line-soft px-4 py-2.5 text-xs text-faint">
          <span class="num">共 {{ data?.total ?? 0 }} 条</span>
          <div class="flex items-center gap-3">
            <button type="button" :disabled="page <= 1" class="transition-colors hover:text-ink disabled:opacity-40" @click="applyQuery({ page: String(page - 1) })">« 上一页</button>
            <span class="mono tabular-nums">{{ page }} / {{ totalPages }}</span>
            <button type="button" :disabled="page >= totalPages" class="transition-colors hover:text-ink disabled:opacity-40" @click="applyQuery({ page: String(page + 1) })">下一页 »</button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
