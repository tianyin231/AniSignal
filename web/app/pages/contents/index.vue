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
const requestedPage = computed(() => Math.max(1, Number(route.query.page) || 1))
const platform = computed(() => String(route.query.platform || ''))
const q = computed(() => String(route.query.q || ''))
const tab = computed(() => String(route.query.tab || 'latest'))
const searchInput = ref(q.value)
watch(q, (v) => (searchInput.value = v))
const pageSize = 30
// PERF-039：服务端 offset 上界 10_000（见 app/api/public.py）。只钳下界时
// `/contents?page=99999` 会真的发出 offset=2,999,940——InnoDB 必须读取并丢弃前 300 万行，
// 而这是无需认证的公开端点。这里按 totalPages 钳上界（正确范式：同仓库 account.vue:180）。
const total = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const page = computed(() => Math.min(requestedPage.value, totalPages.value))

const { api } = usePublicApi()
// community=all（首页聚合进入）时跨分区展示并逐行标注分区
const { scope, isAll } = useCommunityScope()
const { data, status } = await useAsyncData(
  () => `contents-${scope.value}-${platform.value}-${q.value}-${page.value}-${tab.value}`,
  () =>
    tab.value === 'trending'
      ? Promise.resolve({ items: [], total: 0 })
      : api<PublicContentList>('/api/contents', {
          query: {
            community: scope.value,
            limit: pageSize,
            offset: (page.value - 1) * pageSize,
            ...(platform.value ? { platform: platform.value } : {}),
            ...(q.value ? { q: q.value } : {}),
          },
        }),
  { watch: [scope, platform, q, page, tab] },
)
const trendingData = await useAsyncData(
  () => `trending-${scope.value}`,
  () => api<PublicContentList>('/api/trending', { query: { community: scope.value, hours: 24, limit: 20 } }),
  { watch: [scope] },
)

const list = computed(() =>
  tab.value === 'trending'
    ? (trendingData.value?.items ?? []).map(toRawItem)
    : (data.value?.items ?? []).map(toRawItem),
)
const trendingDelta = computed(() =>
  new Map((trendingData.value?.items ?? []).map(item => [item.id, item.delta_view ?? 0])),
)
// total 用独立 ref 而不是 computed：data 依赖 page、page 又依赖 totalPages，
// 直接写成 computed 会被 Vue 判成循环计算。watch 是副作用，不成环。
watch(data, (d) => (total.value = d?.total ?? 0), { immediate: true })

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
    <BackToChannelHome />
    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <form class="flex items-center gap-2" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
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

    <section v-if="tab !== 'trending' && status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>
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
