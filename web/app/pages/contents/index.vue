<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import type { PublicContentList } from '~/composables/usePublicApi'
import { toRawItem } from '~/utils/public-utils'
import { platformsForCommunity, useCommunityScope } from '~/composables/useGameSection'
import { platformMeta, type Platform } from '~/utils/data'

useSeoMeta({ title: '内容库 · AniSignal' })
const { t } = useSiteConfig()

const route = useRoute()
const router = useRouter()
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const platform = computed(() => String(route.query.platform || ''))
const q = computed(() => String(route.query.q || ''))
const tab = computed(() => String(route.query.tab || 'latest'))
const searchInput = ref(q.value)
watch(q, (v) => (searchInput.value = v))
const pageSize = 30

const { api } = usePublicApi()
// community=all（首页聚合进入）时跨分区展示并逐行标注分区
const { scope, isAll } = useCommunityScope()
const contentsRequest = useLazyAsyncData(
  () => `contents-${scope.value}-${platform.value}-${q.value}-${page.value}-${tab.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/contents', {
    signal,
    query: {
      community: scope.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
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
const [{ data, status, error }, { data: trendingData, status: trendingStatus, error: trendingError }] = await Promise.all([contentsRequest, trendingRequest])
const retry = () => tab.value === 'trending' ? trendingRequest.refresh() : contentsRequest.refresh()

const list = computed(() =>
  tab.value === 'trending'
    ? (trendingData.value?.items ?? []).map(toRawItem)
    : (data.value?.items ?? []).map(toRawItem),
)
const trendingDelta = computed(() =>
  new Map((trendingData.value?.items ?? []).map(item => [String(item.id), item.delta_view ?? 0])),
)
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))

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
    <SectionNav />
    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <form v-if="tab !== 'trending'" class="flex items-center gap-2" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
        <div class="flex items-center gap-1.5 rounded-md border border-slate-900/10 bg-white/70 px-2.5 py-1.5 dark:border-white/15 dark:bg-white/5">
          <Search :size="13" class="text-slate-400" />
          <input v-model="searchInput" placeholder="标题关键词" class="w-36 bg-transparent text-xs outline-none placeholder:text-slate-400 sm:w-48" >
        </div>
        <button type="submit" class="btn-primary px-3 py-1.5 text-xs">搜索</button>
      </form>
      <div class="flex gap-1.5">
        <button
          class="rounded-full px-3 py-1 text-xs transition-colors"
          :class="tab !== 'trending' ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
          @click="applyQuery({ tab: undefined })"
        >
          最新
        </button>
        <button
          class="rounded-full px-3 py-1 text-xs transition-colors"
          :class="tab === 'trending' ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
          @click="applyQuery({ tab: 'trending' })"
        >
          🔥 热度飙升
        </button>
      </div>
    </div>

    <div v-if="tab !== 'trending'" class="mb-3 flex flex-wrap gap-1.5">
      <button
        class="rounded-full px-3 py-1 text-xs transition-colors"
        :class="!platform ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
        @click="applyQuery({ platform: undefined })"
      >
        全部平台
      </button>
      <button
        v-for="key in visiblePlatformTabs"
        :key="key"
        class="rounded-full px-3 py-1 text-xs transition-colors"
        :class="platform === key ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
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
        {{ tab === 'trending' ? '24 小时内还没有明显的热度变化。' : '没有匹配的内容。' }}
      </p>
      <div v-if="tab !== 'trending' && totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
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
