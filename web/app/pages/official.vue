<script setup lang="ts">
import { events, officialFeed, type Region } from '~/utils/data'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'cn', label: '国服' },
  { key: 'global', label: '国际服' },
  { key: 'bilibili', label: 'B站' },
  { key: 'weibo', label: '微博' },
  { key: 'x', label: 'X' },
  { key: 'youtube', label: 'YouTube' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'all'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const view = computed({
  get: () => String(route.query.view || 'merged'),
  set: (v: string) => router.replace({ query: { ...route.query, view: v } }),
})

const official = [...events]
  .filter((e) => e.identity === 'official')
  .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))

const mergedList = computed(() => {
  const k = tab.value
  if (k === 'all') return official
  if (k === 'cn' || k === 'global') return official.filter((e) => e.regions.includes(k as Region))
  return official.filter((e) => e.sources.some((s) => s.platform === k))
})
const rawList = computed(() => {
  const k = tab.value
  if (k === 'all') return officialFeed
  if (k === 'cn' || k === 'global') return officialFeed.filter((i) => i.region === k)
  return officialFeed.filter((i) => i.platform === k)
})

useSeoMeta({ title: '官方动态' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <p class="text-xs text-slate-400 dark:text-zinc-500">跨平台重复公告已合并；海外内容提供机翻并保留原文。</p>
      <div class="flex rounded-lg bg-slate-200/60 p-0.5 text-xs dark:bg-zinc-800/70">
        <button
          v-for="v in [
            { k: 'merged', l: '合并视图' },
            { k: 'raw', l: '原始视图' },
          ]"
          :key="v.k"
          type="button"
          class="rounded-md px-2.5 py-1 transition-all"
          :class="
            view === v.k
              ? 'bg-white font-medium text-blue-700 shadow-sm dark:bg-zinc-700 dark:text-blue-300'
              : 'text-slate-500 hover:text-slate-700 dark:text-zinc-400 dark:hover:text-zinc-200'
          "
          @click="view = v.k"
        >
          {{ v.l }}
        </button>
      </div>
    </div>

    <section v-if="view === 'merged'" class="panel overflow-hidden">
      <EventRow v-for="e in mergedList" :key="e.id" :event="e" />
      <p v-if="!mergedList.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此类官方事件。
      </p>
    </section>
    <section v-else class="panel overflow-hidden">
      <RawFeedRow v-for="i in rawList" :key="i.id" :item="i" />
      <p v-if="!rawList.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此筛选下的内容。
      </p>
    </section>
  </div>
</template>
