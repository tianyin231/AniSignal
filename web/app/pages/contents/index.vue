<script setup lang="ts">
import { BookOpen, Flame, Layers, Plus, Search } from 'lucide-vue-next'
import type { PublicContentList } from '~/composables/usePublicApi'
import { toRawItem } from '~/utils/public-utils'
import { communityClasses, communityMeta, platformsForCommunity, useCommunityScope } from '~/composables/useGameSection'
import { platformMeta, type Platform } from '~/utils/data'
import { fmtTime } from '~/utils/admin-types'
import type { TrendingEditionsResponse, TrendingPeriod } from '~/utils/git-trending'

const route = useRoute()
const router = useRouter()
const pageSize = 30
// 后端公开列表的分页上界是 10000（MAX_PUBLIC_OFFSET），折算成页数后前后端一致；
// 不夹这一层的话，超过该页时请求会 422，页面却还显示「下一页」
const MAX_PAGE = Math.floor(10_000 / pageSize) + 1
const platform = computed(() => String(route.query.platform || ''))
const q = computed(() => String(route.query.q || ''))
const { scope, isAll } = useCommunityScope()
const isXiaoheihe = computed(() => scope.value === 'xiaoheihe')
const page = computed(() => Math.min(MAX_PAGE, Math.max(1, Number(route.query.page) || 1)))
const topic = computed(() => {
  const meta = communityMeta(scope.value)
  return meta?.domain === 'feed' ? meta : undefined
})
const topicAccent = computed(() => (topic.value ? communityClasses(topic.value.color) : null))
const sort = computed(() => String(route.query.sort || 'latest'))
const tab = computed(() => topic.value ? 'latest' : String(route.query.tab || 'latest'))
const trendingPeriod = computed(() => {
  const raw = String(route.query.period || '')
  return (['daily', 'weekly', 'monthly'].includes(raw) ? raw : '') as TrendingPeriod | ''
})
const trendingIssue = computed(() => String(route.query.issue || ''))
const searchInput = ref(q.value)
watch(q, (v) => (searchInput.value = v))

useSeoMeta({
  title: () => `${topic.value?.label ?? '内容库'} · AniSignal`,
  description: () => topic.value ? `${topic.value.label}主题流动态、去重归档与持续追踪。` : '多平台采集的官方与社区内容，可按分区、平台和时间筛选。',
})

const { api } = usePublicApi()
const shouldCheckGitTrending = computed(() => Boolean(topic.value && scope.value !== 'xiaoheihe'))
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
  { enabled: shouldCheckGitTrending },
)
const contentsRequest = useLazyAsyncData(
  () => `contents-${scope.value}-${platform.value}-${q.value}-${page.value}-${tab.value}-${sort.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/contents', {
    signal,
    query: {
      community: scope.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      ...(sort.value === 'hot' ? { sort: 'hot' } : {}),
      ...(platform.value ? { platform: platform.value } : {}),
      ...(q.value ? { q: q.value } : {}),
    },
  }),
  { enabled: computed(() => tab.value !== 'trending') },
)
const trendingRequest = useLazyAsyncData(
  () => `trending-${scope.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/trending', { query: { community: scope.value, hours: 24, limit: 20 }, signal }),
  { enabled: computed(() => tab.value === 'trending'), watch: [tab] },
)
const [{ data, status, error }, { data: trendingData, status: trendingStatus, error: trendingError }, { data: gitTrendingData, status: gitTrendingStatus }] = await Promise.all([
  contentsRequest,
  trendingRequest,
  gitTrendingRequest,
])

const hasGitEditions = computed(() => Boolean(shouldCheckGitTrending.value && gitTrendingData.value?.has_editions))
const showGitDesk = computed(() => {
  if (!hasGitEditions.value) return false
  if (route.query.view === 'feed') return false
  if (q.value || platform.value || sort.value === 'hot') return false
  return true
})
const feedStatus = computed(() => data.value?.feed_status)
const latestTopicUpdatedAt = computed(() => {
  if (feedStatus.value?.updated_at) return feedStatus.value.updated_at
  if (gitTrendingData.value?.current?.captured_at) return gitTrendingData.value.current.captured_at
  return data.value?.items?.[0]?.collected_at ?? null
})
const retry = () => tab.value === 'trending' ? trendingRequest.refresh() : contentsRequest.refresh()

const list = computed(() =>
  tab.value === 'trending'
    ? (trendingData.value?.items ?? []).map(toRawItem)
    : (data.value?.items ?? []).map(toRawItem),
)
const trendingDelta = computed(() =>
  new Map((trendingData.value?.items ?? []).map(item => [String(item.id), item.delta_view ?? 0])),
)
const totalPages = computed(() => Math.min(MAX_PAGE, Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize))))

// 平台筛选随分区变化（映射统一在 useGameSection）：碧蓝航线当前只有 B 站来源
const visiblePlatformTabs = computed(() =>
  platformsForCommunity(scope.value).filter(key => key in platformMeta),
)
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
    applyQuery({ view: undefined, q: undefined, platform: undefined, sort: undefined })
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

const customCreateTo = computed(() =>
  isXiaoheihe.value ? '/account?tab=custom&create=1&source=xiaoheihe' : '/account?tab=custom&create=1',
)
</script>

<template>
  <div class="space-y-4">
    <!-- 主题流频道头（统一升级小黑盒、开源及所有自定义 feed 分区） -->
    <header v-if="topic" class="wall-heading flex flex-wrap items-end justify-between gap-3">
      <div class="min-w-0">
        <div class="flex items-center gap-2.5">
          <span v-if="topicAccent" class="size-2.5 shrink-0 rounded-full" :class="topicAccent.dot" aria-hidden="true" />
          <h1 class="text-2xl font-bold tracking-tight text-ink sm:text-[26px]">{{ topic.label }}</h1>
          <span v-if="topic.en && topic.en !== topic.label" class="mono hidden text-xs uppercase tracking-wider text-faint sm:inline">{{ topic.en }}</span>
        </div>
        <div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <span v-if="data?.total">已收录 <strong class="num font-semibold text-ink">{{ data.total.toLocaleString('zh-CN') }}</strong> 项</span>
          <time v-if="latestTopicUpdatedAt" class="mono tabular-nums" :datetime="latestTopicUpdatedAt">更新于 {{ fmtTime(latestTopicUpdatedAt) }}</time>
          <span v-if="feedStatus?.stale" class="font-medium text-warn" role="status">更新暂缓</span>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <div
          v-if="hasGitEditions"
          class="inline-flex rounded-[10px] border border-line bg-card p-[3px]"
          role="tablist"
          aria-label="视图模式"
        >
          <button
            type="button"
            role="tab"
            :aria-selected="showGitDesk"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors"
            :class="showGitDesk ? 'bg-ink text-card shadow-sm' : 'text-muted hover:text-ink'"
            @click="switchFeedView('editions')"
          >
            <BookOpen :size="13" aria-hidden="true" />趋势刊物
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="!showGitDesk"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors"
            :class="!showGitDesk ? 'bg-ink text-card shadow-sm' : 'text-muted hover:text-ink'"
            @click="switchFeedView('feed')"
          >
            <Layers :size="13" aria-hidden="true" />全部收录
          </button>
        </div>

        <NuxtLink :to="customCreateTo" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5 text-sm">
          <Plus :size="15" aria-hidden="true" />定制监控
        </NuxtLink>
      </div>
    </header>

    <!-- 游戏分区内容库导航 -->
    <template v-else>
      <h1 class="sr-only">内容库</h1>
      <SectionNav />
    </template>

    <!-- 专属趋势刊物台（当该主题流拥有 GitHub Trending 归档时） -->
    <GitTrendingDesk
      v-if="showGitDesk && gitTrendingData"
      :data="gitTrendingData"
      :loading="gitTrendingStatus === 'pending'"
      @select-period="selectTrendingPeriod"
      @select-issue="selectTrendingIssue"
    />

    <!-- 升级后的通用主题流 & 内容库视图 -->
    <template v-else>
      <section class="panel p-3.5 sm:p-4">
        <div class="flex flex-wrap items-center justify-between gap-2.5">
          <form v-if="tab !== 'trending'" class="flex min-w-0 flex-1 items-center gap-2 sm:flex-initial" role="search" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
            <div class="relative min-w-0 flex-1 sm:flex-initial">
              <Search :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
              <input v-model="searchInput" type="search" aria-label="标题关键词" placeholder="搜索标题或项目名" class="input h-9 w-full pl-9 text-[13px] sm:w-64">
            </div>
            <button type="submit" class="btn-primary h-9 shrink-0 px-3.5 text-xs">搜索</button>
          </form>

          <div v-if="topic" class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="排序">
            <button
              type="button"
              role="tab"
              :aria-selected="sort !== 'hot'"
              class="h-8 rounded-lg px-3.5 text-[13px] transition-colors"
              :class="sort !== 'hot' ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
              @click="applyQuery({ sort: undefined })"
            >
              最新
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="sort === 'hot'"
              class="inline-flex h-8 items-center gap-1 rounded-lg px-3.5 text-[13px] transition-colors"
              :class="sort === 'hot' ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
              @click="applyQuery({ sort: 'hot' })"
            >
              <Flame :size="14" class="text-hot" aria-hidden="true" />热门
            </button>
          </div>
          <div v-else class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="排序">
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
          {{ isXiaoheihe ? '暂无小黑盒帖子。' : tab === 'trending' ? '24 小时内还没有明显的热度变化。' : '没有匹配的内容。' }}
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
