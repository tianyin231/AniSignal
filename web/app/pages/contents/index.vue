<script setup lang="ts">
import { Flame, Plus, Search } from 'lucide-vue-next'
import type { PublicContentList } from '~/composables/usePublicApi'
import { toRawItem } from '~/utils/public-utils'
import { communityMeta, platformsForCommunity, useCommunityScope } from '~/composables/useGameSection'
import { platformMeta, type Platform } from '~/utils/data'
import { fmtTime } from '~/utils/admin-types'

const { t } = useSiteConfig()

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
const page = computed(() => isXiaoheihe.value ? 1 : Math.min(MAX_PAGE, Math.max(1, Number(route.query.page) || 1)))
const topic = computed(() => {
  const meta = communityMeta(scope.value)
  return meta?.domain === 'feed' ? meta : undefined
})
const tab = computed(() => topic.value ? 'latest' : String(route.query.tab || 'latest'))
const searchInput = ref(q.value)
watch(q, (v) => (searchInput.value = v))

useSeoMeta({
  title: () => `${isXiaoheihe.value ? '小黑盒热帖' : topic.value?.label ?? '内容库'} · AniSignal`,
  description: () => topic.value ? undefined : '多平台采集的官方与社区内容，可按分区、平台和时间筛选。',
})

const { api } = usePublicApi()
const contentsRequest = useLazyAsyncData(
  () => `contents-${scope.value}-${platform.value}-${q.value}-${page.value}-${tab.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/contents', {
    signal,
    query: {
      community: scope.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      ...(isXiaoheihe.value ? { sort: 'hot' } : {
        ...(platform.value ? { platform: platform.value } : {}),
        ...(q.value ? { q: q.value } : {}),
      }),
    },
  }),
  { enabled: computed(() => tab.value !== 'trending') },
)
const trendingRequest = useLazyAsyncData(
  () => `trending-${scope.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/trending', { query: { community: scope.value, hours: 24, limit: 20 }, signal }),
  { enabled: computed(() => tab.value === 'trending'), watch: [tab] },
)
const [{ data, status, error }, { data: trendingData, status: trendingStatus, error: trendingError }] = await Promise.all([contentsRequest, trendingRequest])
const feedStatus = computed(() => data.value?.feed_status)
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
</script>

<template>
  <div>
    <header v-if="isXiaoheihe" class="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div class="wall-surface px-1">
        <h1 class="text-2xl font-bold text-ink">{{ feedStatus?.automatic === false ? '小黑盒精选' : '小黑盒热帖' }}</h1>
        <div v-if="feedStatus?.updated_at || feedStatus?.stale" class="mt-1.5 flex flex-wrap gap-x-3 text-xs text-muted">
          <time v-if="feedStatus.updated_at" class="mono" :datetime="feedStatus.updated_at">更新于 {{ fmtTime(feedStatus.updated_at) }}</time>
          <span v-if="feedStatus.stale" class="text-warn" role="status">更新暂缓</span>
        </div>
      </div>
      <NuxtLink to="/account?tab=custom&create=1&source=xiaoheihe" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5">
        <Plus :size="15" aria-hidden="true" />关注热帖
      </NuxtLink>
    </header>
    <h1 v-else-if="topic" class="wall-heading mb-5 text-2xl font-bold text-ink">{{ topic.label }}</h1>
    <template v-else>
      <h1 class="sr-only">内容库</h1>
      <SectionNav />
    </template>
    <div v-if="!isXiaoheihe" class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <form v-if="tab !== 'trending'" class="flex items-center gap-2" role="search" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
        <div class="relative">
          <Search :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
          <input v-model="searchInput" type="search" aria-label="标题关键词" placeholder="标题关键词" class="input h-10 w-44 pl-9 text-[13px] sm:w-60">
        </div>
        <button type="submit" class="btn-primary h-10 px-4">搜索</button>
      </form>
      <div v-if="!topic" class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="排序">
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

    <div v-if="!isXiaoheihe && tab !== 'trending'" class="mb-4 flex flex-wrap gap-1.5" aria-label="平台筛选">
      <button
        type="button"
        class="h-8 rounded-full px-3.5 text-[13px] transition-colors"
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
        class="h-8 rounded-full px-3.5 text-[13px] transition-colors"
        :class="platform === key ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
        :aria-pressed="platform === key"
        @click="applyQuery({ platform: key })"
      >
        {{ platformMeta[key as Platform]?.label }}
      </button>
    </div>

    <section v-if="(tab === 'trending' ? trendingStatus : status) === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>
    <section v-else-if="tab === 'trending' ? trendingError : error" role="alert" class="panel px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">内容暂时加载失败。<button type="button" class="ml-1 underline" @click="retry">重新加载</button></section>
    <section v-else class="panel overflow-hidden">
      <RawFeedRow
        v-for="item in list"
        :key="item.id"
        :item="item"
        :show-community="isAll"
        :delta-view="tab === 'trending' ? trendingDelta.get(item.id) ?? 0 : 0"
      />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        {{ isXiaoheihe ? '暂无小黑盒帖子。' : tab === 'trending' ? '24 小时内还没有明显的热度变化。' : '没有匹配的内容。' }}
      </p>
      <div v-if="!isXiaoheihe && tab !== 'trending' && totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ data?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="applyQuery({ page: String(page - 1) })">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="applyQuery({ page: String(page + 1) })">下一页 »</button>
        </div>
      </div>
    </section>
  </div>
</template>
