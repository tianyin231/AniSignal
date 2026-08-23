<script setup lang="ts">
import type { ApiEventList } from '~/composables/usePublicApi'
import { categoryMeta, toAppEvent } from '~/utils/public-utils'
import type { AppEvent } from '~/utils/data'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'official_announce', label: '公告' },
  { key: 'delay', label: '延期' },
  { key: 'arrived', label: '到货' },
  { key: 'unboxing', label: '开箱' },
  { key: 'quality', label: '质量' },
  { key: 'version_update', label: '版本' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'all'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30

const { api } = usePublicApi()
const { data, status } = await useAsyncData(
  () => `events-${tab.value}-${page.value}`,
  () =>
    api<ApiEventList>('/api/events', {
      query: {
        limit: pageSize,
        offset: (page.value - 1) * pageSize,
        ...(tab.value !== 'all' ? { category: tab.value } : {}),
      },
    }),
  { watch: [tab, page] },
)

const list = computed<AppEvent[]>(() => (data.value?.items ?? []).map(toAppEvent))
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))

function goPage(p: number) {
  router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
}

useSeoMeta({ title: '事件库' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <section v-else class="panel overflow-hidden">
      <NuxtLink v-for="e in list" :key="e.id" :to="`/events/${e.id}`" class="block">
        <EventRow :event="e" />
      </NuxtLink>
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此类事件。
      </p>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ data?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </section>
  </div>
</template>
