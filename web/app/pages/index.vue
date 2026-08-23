<script setup lang="ts">
import type { ApiEventList, PublicContentList, PublicStats } from '~/composables/usePublicApi'
import { fmtTime, toAppEvent, toRawItem } from '~/utils/public-utils'
import type { Platform, RawItem, Region } from '~/utils/data'

const tabs = [
  { key: 'latest', label: '最新' },
  { key: 'official', label: '官方' },
  { key: 'important', label: '重要事件' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'latest'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30

const { api } = usePublicApi()
const { t } = useSiteConfig()
const { data: feed, status } = await useAsyncData(
  () => `home-feed-${tab.value}-${page.value}`,
  () =>
    api<PublicContentList>('/api/contents', {
      query: {
        limit: pageSize,
        offset: (page.value - 1) * pageSize,
        ...(tab.value === 'official' ? { identity: 'official' } : {}),
      },
    }),
  { watch: [tab, page] },
)
const { data: importantData, status: importantStatus } = await useAsyncData(
  () => `home-important-${page.value}`,
  () =>
    api<ApiEventList>('/api/events', {
      query: { importance: true, limit: pageSize, offset: (page.value - 1) * pageSize },
    }),
  { watch: [page] },
)
const { data: stats } = await useAsyncData('home-stats', () => api<PublicStats>('/api/stats'))

const totalPages = computed(() =>
  Math.max(1, Math.ceil(((tab.value === 'important' ? importantData.value?.total : feed.value?.total) ?? 0) / pageSize)),
)
const list = computed(() => (feed.value?.items ?? []).map(toRawItem))
const importantList = computed(() => (importantData.value?.items ?? []).map(toAppEvent))

function goPage(p: number) {
  router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
}

useSeoMeta({ title: '今日情报' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="stats" class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-slate-400 dark:text-zinc-500">
      <span>已入库 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.total_contents }}</span> 条</span>
      <span>官方 <span class="mono text-slate-500 dark:text-zinc-400">{{ stats.official_contents }}</span> 条</span>
      <span v-if="stats.latest_collected_at">最近采集 {{ fmtTime(stats.latest_collected_at) }}</span>
    </section>
    <p class="mb-3 px-1 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">{{ t('home_intro') }}</p>

    <section v-if="status === 'pending' || importantStatus === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>

    <!-- 重要事件：聚合后的结论流 -->
    <section v-else-if="tab === 'important'" class="panel overflow-hidden">
      <NuxtLink v-for="e in importantList" :key="e.id" :to="`/events/${e.id}`" class="block">
        <EventRow :event="e" />
      </NuxtLink>
      <p v-if="!importantList.length" class="px-4 py-10 text-center text-sm text-slate-400">
        暂无标记为重要的事件；引擎会在履约状态推进与官方重要公告时自动标记。
      </p>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ importantData?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </section>

    <!-- 内容流 -->
    <section v-else class="panel overflow-hidden">
      <NuxtLink v-for="entry in list.map((row) => ({ row, url: feed!.items.find((i) => String(i.id) === row.id)?.url ?? null }))" :key="entry.row.id" :to="`/contents/${entry.row.id}`" class="block">
        
      </NuxtLink>
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无内容。
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

    <div class="mt-6 space-y-4 xl:hidden">
      <StatusCard />
      <ReportCard />
    </div>
  </div>
</template>
