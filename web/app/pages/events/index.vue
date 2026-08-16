<script setup lang="ts">
import { events } from '~/utils/data'

const tabs = [
  { key: 'all', label: '全部' },
  { key: '公告', label: '公告' },
  { key: '延期', label: '延期' },
  { key: '到货', label: '到货' },
  { key: '开箱', label: '开箱' },
  { key: '质量', label: '质量' },
  { key: '更正', label: '更正' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'all'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})

const all = [...events].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
const list = computed(() => (tab.value === 'all' ? all : all.filter((e) => e.category === tab.value)))

useSeoMeta({ title: '事件库' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />
    <section class="panel overflow-hidden">
      <EventRow v-for="e in list" :key="e.id" :event="e" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此类事件。
      </p>
      <div
        class="flex items-center justify-between border-t border-slate-200/70 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500"
      >
        <span>第 1 页 · 共 {{ list.length }} 条</span>
        <span class="mono">« 1 »</span>
      </div>
    </section>
  </div>
</template>
