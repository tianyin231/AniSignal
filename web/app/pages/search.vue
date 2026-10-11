<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import type { ApiEventList, PublicContentList } from '~/composables/usePublicApi'
import { toAppEvent, toRawItem } from '~/utils/public-utils'
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

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
  router.replace({ query: { ...route.query, q: input.value.trim(), page: undefined } })
}

const tab = computed({
  get: () => String(route.query.tab || 'events'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v, page: undefined } }),
})
const q = computed(() => String(route.query.q || ''))

const { api } = usePublicApi()
const community = computed({
  get: () => GAME_COMMUNITIES.some(c => c.key === route.query.community) ? String(route.query.community) : 'all',
  set: (value: string) => router.replace({ query: { ...route.query, community: value, page: undefined } }),
})
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const eventsRequest = useLazyAsyncData(
  () => `search-events-${community.value}-${q.value}-${page.value}`,
  (_app, { signal }) => (q.value ? api<ApiEventList>('/api/events', { query: { community: community.value, q: q.value, limit: 30, offset: (page.value - 1) * 30 }, signal }) : Promise.resolve({ items: [], total: 0 })),
  { watch: [community, q, page] },
)
const contentsRequest = useLazyAsyncData(
  () => `search-contents-${community.value}-${q.value}-${page.value}`,
  (_app, { signal }) => (q.value ? api<PublicContentList>('/api/contents', { query: { community: community.value, q: q.value, limit: 30, offset: (page.value - 1) * 30 }, signal }) : Promise.resolve({ items: [], total: 0 })),
  { watch: [community, q, page] },
)
const [
  { data: eventsData, status: eventsStatus, error: eventsError, refresh: refreshEvents },
  { data: contentsData, status: contentsStatus, error: contentsError, refresh: refreshContents },
] = await Promise.all([eventsRequest, contentsRequest])
const events = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const contents = computed(() => (contentsData.value?.items ?? []).map(toRawItem))
const total = computed(() => (tab.value === 'events' ? eventsData.value?.total : contentsData.value?.total) ?? 0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / 30)))

const hotWords = ['延期', '到货', '开箱', '质量', '版本']

useSeoMeta({ title: '搜索' })
</script>

<template>
  <div>
    <form class="panel flex flex-wrap items-center gap-2 p-2 pl-3.5" @submit.prevent="submit">
      <Search :size="16" class="shrink-0 text-slate-400" />
      <input
        v-model="input"
        type="search"
        placeholder="搜索事件与内容（标题关键词）"
        class="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-zinc-100"
      >
      <button type="submit" class="btn-primary shrink-0 px-3.5 py-1.5">搜索</button>
      <select v-model="community" aria-label="搜索范围" class="select w-full px-2 py-1.5 text-sm sm:w-auto">
        <option value="all">全站</option>
        <option v-for="c in GAME_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
      </select>
    </form>

    <div class="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
      <span>热门：</span>
      <button
        v-for="w in hotWords"
        :key="w"
        class="rounded bg-slate-500/10 px-2 py-0.5 transition-colors hover:text-slate-600 dark:bg-zinc-500/10 dark:hover:text-zinc-300"
        @click="router.replace({ query: { ...route.query, q: w, page: undefined } })"
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
      <p v-else-if="eventsError" role="alert" class="px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">搜索暂时失败。<button type="button" class="ml-1 underline" @click="refreshEvents()">重试</button></p>
      <template v-else>
        <EventRow v-for="e in events" :key="e.id" :event="e" :show-community="community === 'all'" />
        <p v-if="!events.length" class="px-4 py-10 text-center text-sm text-slate-400">
          没有匹配的事件；可试试「内容」页签查看原始内容。
        </p>
      </template>
    </section>
    <section v-else class="panel mt-4 overflow-hidden">
      <p v-if="contentsStatus === 'pending'" class="px-4 py-10 text-center text-sm text-slate-400">搜索中…</p>
      <p v-else-if="contentsError" role="alert" class="px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">搜索暂时失败。<button type="button" class="ml-1 underline" @click="refreshContents()">重试</button></p>
      <template v-else>
        <RawFeedRow v-for="c in contents" :key="c.id" :item="c" :show-community="community === 'all'" />
        <p v-if="!contents.length" class="px-4 py-10 text-center text-sm text-slate-400">没有匹配的内容。</p>
      </template>
    </section>
    <nav v-if="q && totalPages > 1" aria-label="搜索结果分页" class="mt-4 flex items-center justify-between text-xs text-slate-500 dark:text-zinc-400">
      <span>共 {{ total }} 条</span>
      <div class="flex items-center gap-3">
        <button :disabled="page <= 1" class="disabled:opacity-40" @click="router.replace({ query: { ...route.query, page: String(page - 1) } })">上一页</button>
        <span>{{ page }} / {{ totalPages }}</span>
        <button :disabled="page >= totalPages" class="disabled:opacity-40" @click="router.replace({ query: { ...route.query, page: String(page + 1) } })">下一页</button>
      </div>
    </nav>
  </div>
</template>
