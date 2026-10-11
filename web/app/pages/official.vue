<script setup lang="ts">
import { toRawItem } from '~/utils/public-utils'
import { platformsForCommunity, useCommunityScope } from '~/composables/useGameSection'
import type { PublicContentList } from '~/composables/usePublicApi'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'bilibili', label: 'B站' },
  { key: 'miyoushe', label: '米游社' },
  { key: 'kuro', label: '库街区' },
  { key: 'youtube', label: 'YouTube' }
]
const route = useRoute()
const router = useRouter()
const { scope, isAll } = useCommunityScope()
// 平台筛选随视角变化（映射统一在 useGameSection）：聚合=all 并集，分区各自收敛
const visibleTabs = computed(() => {
  const allowed = platformsForCommunity(scope.value)
  return tabs.filter(t => t.key === 'all' || allowed.includes(t.key))
})
const tab = computed({
  get: () => visibleTabs.value.some(item => item.key === route.query.tab) ? String(route.query.tab) : 'all',
  set: (v: string) => router.replace({ query: { ...route.query, tab: v === 'all' ? undefined : v, page: undefined } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30

const { api } = usePublicApi()
const { data, status, error, refresh } = await useLazyAsyncData(
  () => `official-feed-${scope.value}-${tab.value}-${page.value}`,
  (_app, { signal }) =>
    api<PublicContentList>('/api/contents', {
      signal,
      query: {
        community: scope.value, identity: 'official',
        ...(tab.value === 'all' ? {} : { platform: tab.value }),
        limit: pageSize, offset: (page.value - 1) * pageSize,
      },
    }),
)
const list = computed(() => (data.value?.items ?? []).map(toRawItem))
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))
const goPage = (value: number) => router.replace({ query: { ...route.query, page: value > 1 ? value : undefined } })

useSeoMeta({ title: '官方动态' })
</script>

<template>
  <div>
    <BackToChannelHome />
    <TabNav v-model="tab" :tabs="visibleTabs" />

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <section v-else-if="error" role="alert" class="panel px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">官方动态暂时加载失败。<button type="button" class="ml-1 underline" @click="refresh()">重新加载</button></section>
    <section v-else class="panel overflow-hidden">
      <RawFeedRow v-for="row in list" :key="row.id" :item="row" :show-community="isAll" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此筛选下的官方内容。
      </p>
    </section>
    <nav v-if="status === 'success' && data?.total" aria-label="官方动态分页" class="mt-4 flex items-center justify-between px-1 text-xs text-slate-500 dark:text-zinc-400">
      <span>共 {{ data.total }} 条</span>
      <div v-if="totalPages > 1" class="flex items-center gap-3">
        <button :disabled="page <= 1" class="min-h-11 disabled:opacity-40" @click="goPage(page - 1)">上一页</button>
        <span>{{ page }} / {{ totalPages }}</span>
        <button :disabled="page >= totalPages" class="min-h-11 disabled:opacity-40" @click="goPage(page + 1)">下一页</button>
      </div>
    </nav>
  </div>
</template>
