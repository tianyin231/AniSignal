<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import type { ApiEventList, PublicContentList } from '~/composables/usePublicApi'
import { toAppEvent, toRawItem } from '~/utils/public-utils'

const route = useRoute()
const router = useRouter()
const input = ref(String(route.query.q || ''))
watch(
  () => route.query.q,
  (v) => {
    input.value = String(v || '')
  },
)
const submit = () => {
  if (!input.value.trim()) return
  router.replace({ query: { ...route.query, q: input.value.trim() } })
}

const tab = computed({
  get: () => String(route.query.tab || 'events'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const q = computed(() => String(route.query.q || ''))

const { api } = usePublicApi()
const { data: eventsData, status: eventsStatus } = await useAsyncData(
  () => `search-events-${q.value}`,
  () => (q.value ? api<ApiEventList>('/api/events', { query: { q: q.value, limit: 30 } }) : Promise.resolve({ items: [], total: 0 })),
  { watch: [q] },
)
const { data: contentsData, status: contentsStatus } = await useAsyncData(
  () => `search-contents-${q.value}`,
  () => (q.value ? api<PublicContentList>('/api/contents', { query: { q: q.value, limit: 30 } }) : Promise.resolve({ items: [], total: 0 })),
  { watch: [q] },
)
const events = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const contents = computed(() => (contentsData.value?.items ?? []).map(toRawItem))

const hotWords = ['延期', '到货', '开箱', '质量', '版本']

useSeoMeta({ title: '搜索' })
</script>

<template>
  <div>
    <form class="panel flex items-center gap-2 p-2 pl-3.5" @submit.prevent="submit">
      <Search :size="16" class="shrink-0 text-slate-400" />
      <input
        v-model="input"
        type="search"
        placeholder="搜索事件与内容（标题关键词）"
        class="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-zinc-100"
      >
      <button type="submit" class="btn-primary shrink-0 px-3.5 py-1.5">搜索</button>
    </form>

    <div class="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
      <span>热门：</span>
      <button
        v-for="w in hotWords"
        :key="w"
        class="rounded bg-slate-500/10 px-2 py-0.5 transition-colors hover:text-slate-600 dark:bg-zinc-500/10 dark:hover:text-zinc-300"
        @click="router.replace({ query: { q: w } })"
      >
        {{ w }}
      </button>
    </div>

    <div class="mt-4 flex rounded-lg bg-slate-200/60 p-0.5 text-xs dark:bg-zinc-800/70">
      <button
        v-for="t in [{ k: 'events', l: `事件 ${eventsData?.total ?? 0}` }, { k: 'contents', l: `内容 ${contentsData?.total ?? 0}` }]"
        :key="t.k"
        type="button"
        class="rounded-md px-2.5 py-1 transition-all"
        :class="tab === t.k ? 'bg-white font-medium text-blue-700 shadow-sm dark:bg-zinc-700 dark:text-blue-300' : 'text-slate-500 hover:text-slate-700 dark:text-zinc-400 dark:hover:text-zinc-200'"
        @click="tab = t.k"
      >
        {{ t.l }}
      </button>
    </div>

    <section v-if="!q" class="panel mt-4 px-4 py-12 text-center text-sm text-slate-400">
      输入关键词搜索事件结论与原始内容。
    </section>
    <section v-else-if="tab === 'events'" class="panel mt-4 overflow-hidden">
      <p v-if="eventsStatus === 'pending'" class="px-4 py-10 text-center text-sm text-slate-400">搜索中…</p>
      <template v-else>
        <NuxtLink v-for="e in events" :key="e.id" :to="`/events/${e.id}`" class="block">
          <EventRow :event="e" />
        </NuxtLink>
        <p v-if="!events.length" class="px-4 py-10 text-center text-sm text-slate-400">
          没有匹配的事件；可试试「内容」页签查看原始内容。
        </p>
      </template>
    </section>
    <section v-else class="panel mt-4 overflow-hidden">
      <p v-if="contentsStatus === 'pending'" class="px-4 py-10 text-center text-sm text-slate-400">搜索中…</p>
      <template v-else>
        <NuxtLink v-for="c in contents" :key="c.id" :to="`/contents/${c.id}`" class="block">
          <RawFeedRow :item="c" />
        </NuxtLink>
        <p v-if="!contents.length" class="px-4 py-10 text-center text-sm text-slate-400">没有匹配的内容。</p>
      </template>
    </section>
  </div>
</template>
