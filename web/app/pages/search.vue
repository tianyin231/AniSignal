<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import { events } from '~/utils/data'

const route = useRoute()
const router = useRouter()
const input = ref(String(route.query.q || ''))
watch(
  () => route.query.q,
  (v) => {
    input.value = String(v || '')
  },
)

const q = computed(() => String(route.query.q || ''))
const results = computed(() => {
  const s = q.value.trim().toLowerCase()
  if (!s) return []
  return events
    .filter((e) => `${e.title} ${e.summary} ${e.category}`.toLowerCase().includes(s))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
})
const hot = ['延期', '到货', '菲比', '质量', '送测']

function go(s: string) {
  const t = s.trim()
  input.value = t
  if (t) router.replace({ query: { q: t } })
}

useSeoMeta({ title: '搜索' })
</script>

<template>
  <div>
    <form class="relative mb-4" @submit.prevent="go(input)">
      <Search :size="15" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
      <input
        v-model="input"
        type="search"
        aria-label="搜索"
        placeholder="搜索事件、商品、主题或账号"
        class="w-full rounded-[14px] border border-slate-200/80 bg-white/60 py-2.5 pl-9 pr-3 text-sm outline-none backdrop-blur placeholder:text-slate-400 focus:border-blue-400 dark:border-zinc-700/60 dark:bg-white/5 dark:text-zinc-200"
        @keydown.enter.prevent="go(input)"
      />
    </form>

    <section v-if="!q" class="panel p-8 text-center">
      <p class="text-sm text-slate-500 dark:text-zinc-400">输入关键词，在已合并的事件中检索。</p>
      <div class="mt-3 flex flex-wrap justify-center gap-1.5">
        <button
          v-for="h in hot"
          :key="h"
          type="button"
          class="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-500 transition-colors hover:border-blue-400/60 hover:text-blue-700 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-blue-300"
          @click="go(h)"
        >
          {{ h }}
        </button>
      </div>
    </section>

    <section v-else class="panel overflow-hidden">
      <p class="border-b border-slate-200/70 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        “{{ q }}” · {{ results.length }} 条结果（在已接入来源的事件中）
      </p>
      <EventRow v-for="e in results" :key="e.id" :event="e" />
      <p v-if="!results.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中未找到相关事件。
      </p>
    </section>
  </div>
</template>
