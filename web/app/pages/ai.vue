<script setup lang="ts">
import { ArrowUpRight, Check, Radio, RefreshCw, Search, Sparkles } from 'lucide-vue-next'
import { utcTimestamp } from '~/utils/public-utils'

interface NewsItem {
  id: number
  headline: string
  summary: string
  original_title: string
  kind: 'report' | 'rumor'
  topics: string[]
  platform: string
  source_name: string
  published_at: string | null
  url: string | null
  references: { url: string; label: string }[]
}
interface NewsList {
  items: NewsItem[]
  total: number
  topics: string[]
  sources: { name: string; platform: string; status: string; last_success_at: string | null; interval_minutes: number }[]
}

useSeoMeta({ title: 'AI 快讯 · AniSignal', description: '追踪 AI 产品与模型动态，区分报道与传闻，保留消息来源与真实发布时间。' })
const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const search = ref(String(route.query.q || ''))
const filters = computed(() => ({
  q: String(route.query.q || '').slice(0, 100),
  topic: String(route.query.topic || ''),
  kind: ['report', 'rumor'].includes(String(route.query.kind)) ? String(route.query.kind) : 'all',
  hours: [24, 168, 720].includes(Number(route.query.hours)) ? Number(route.query.hours) : 168,
}))
const request = computed(() => ({ ...filters.value, limit: 20, offset: (page.value - 1) * 20 }))
const { data, status, error, refresh } = await useAsyncData(
  () => `ai-news-${JSON.stringify(request.value)}`,
  () => api<NewsList>('/api/ai-news', { query: request.value }),
)
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total || 0) / 20)))
const hasFilters = computed(() => !!(filters.value.q || filters.value.topic || filters.value.kind !== 'all'))
const pendingNew = ref(false)
const refreshBusy = ref(false)
const knownTopics = ['OpenAI', 'Codex', 'Claude', 'Gemini', 'DeepSeek', '开源模型', '行业动态']
const kinds = [{ key: 'all', label: '全部快讯' }, { key: 'report', label: '报道' }, { key: 'rumor', label: '传闻线索' }]
const sourceLabels: Record<string, string> = { active: '正常更新', delayed: '更新延迟', pending: '等待首轮' }
const sourceTime = computed(() => {
  const times = (data.value?.sources || []).map(s => s.last_success_at).filter((t): t is string => !!t)
  return times.sort((a, b) => utcTimestamp(b) - utcTimestamp(a))[0]
})

function dateLabel(value: string | null | undefined, timeOnly = false): string {
  if (!value) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false,
    ...(timeOnly ? {} : { month: '2-digit', day: '2-digit' } as const),
    hour: '2-digit', minute: '2-digit',
  }).format(utcTimestamp(value))
}
function filterBy(key: string, value: string | number | undefined) {
  return router.replace({ query: { ...route.query, [key]: value || undefined, page: undefined } })
}
function clearFilters() {
  search.value = ''
  return router.replace({ query: { hours: route.query.hours } })
}
async function reload() {
  refreshBusy.value = true
  try {
    await refresh()
    if (!error.value) pendingNew.value = false
  } finally { refreshBusy.value = false }
}
async function checkUpdates() {
  if (document.visibilityState !== 'visible' || pendingNew.value || page.value !== 1 || refreshBusy.value) return
  try {
    const head = await api<NewsList>('/api/ai-news', { query: { ...filters.value, limit: 1 } })
    if (head.items[0] && head.items[0].id !== data.value?.items[0]?.id) pendingNew.value = true
  } catch { /* 保留正在阅读的列表，用户手动刷新时显示错误。 */ }
}
let timer: ReturnType<typeof setInterval> | undefined
watch(() => route.query.q, value => { search.value = String(value || '') })
watch(request, () => { pendingNew.value = false })
onMounted(() => {
  timer = setInterval(checkUpdates, 60_000)
  document.addEventListener('visibilitychange', checkUpdates)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  document.removeEventListener('visibilitychange', checkUpdates)
})
</script>

<template>
  <div class="mx-auto max-w-[1120px]">
    <header class="mb-7 flex flex-wrap items-end justify-between gap-4 px-1 pt-2">
      <div>
        <div class="mb-2 flex items-center gap-2 text-teal-700 dark:text-teal-300">
          <Radio :size="18" aria-hidden="true" />
          <span class="text-xs font-medium">模型、产品与社区动向</span>
        </div>
        <h1 class="text-[30px] font-semibold tracking-tight text-slate-900 sm:text-[36px] dark:text-zinc-50">AI 快讯</h1>
        <p class="mt-2 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">把新消息捎过来，也把来源留给你。</p>
      </div>
      <button type="button" :disabled="refreshBusy || status === 'pending'" class="inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm text-slate-600 hover:bg-white/50 dark:text-zinc-300 dark:hover:bg-white/5" @click="reload">
        <RefreshCw :size="15" :class="{ 'animate-spin motion-reduce:animate-none': refreshBusy }" aria-hidden="true" />
        {{ refreshBusy ? '正在刷新' : '刷新快讯' }}
      </button>
    </header>

    <div class="grid items-start gap-7 lg:grid-cols-[minmax(0,1fr)_250px]">
      <section class="min-w-0" aria-label="AI 新闻流">
        <div class="panel mb-4 p-4 sm:p-5">
          <form class="flex gap-2" role="search" @submit.prevent="filterBy('q', search.trim())">
            <div class="relative min-w-0 flex-1">
              <Search :size="16" class="pointer-events-none absolute left-3 top-3 text-slate-400" aria-hidden="true" />
              <input v-model="search" maxlength="100" type="search" aria-label="搜索 AI 快讯" placeholder="搜索模型、产品或作者" class="min-h-10 w-full rounded-lg border border-slate-200 bg-white/70 pl-9 pr-3 text-sm outline-offset-2 placeholder:text-slate-400 dark:border-zinc-700 dark:bg-zinc-900/70" />
            </div>
            <button type="submit" class="min-h-10 rounded-lg bg-teal-700 px-4 text-sm font-medium text-white hover:bg-teal-800 dark:bg-teal-600 dark:hover:bg-teal-500">搜索</button>
          </form>
          <div class="mt-3 flex flex-wrap gap-1.5" aria-label="关注话题">
            <button type="button" :aria-pressed="!filters.topic" class="rounded-full px-3 py-1.5 text-xs" :class="!filters.topic ? 'bg-teal-700 text-white dark:bg-teal-600' : 'text-slate-500 hover:bg-slate-100 dark:text-zinc-400 dark:hover:bg-white/5'" @click="filterBy('topic', '')">全部</button>
            <button v-for="topic in data?.topics || knownTopics" :key="topic" type="button" :aria-pressed="filters.topic === topic" class="rounded-full px-3 py-1.5 text-xs" :class="filters.topic === topic ? 'bg-teal-700 text-white dark:bg-teal-600' : 'text-slate-600 hover:bg-slate-100 dark:text-zinc-300 dark:hover:bg-white/5'" @click="filterBy('topic', topic)">{{ topic }}</button>
          </div>
        </div>

        <div class="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
          <nav class="flex gap-4" aria-label="消息类型">
            <button v-for="kind in kinds" :key="kind.key" type="button" :aria-pressed="filters.kind === kind.key" class="min-h-10 border-b-2 text-sm" :class="filters.kind === kind.key ? 'border-teal-600 font-semibold text-teal-800 dark:text-teal-300' : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-zinc-400 dark:hover:text-zinc-100'" @click="filterBy('kind', kind.key)">{{ kind.label }}</button>
          </nav>
          <select :value="filters.hours" aria-label="快讯时间范围" class="min-h-10 rounded-lg bg-transparent px-2 text-xs text-slate-600 dark:text-zinc-300 dark:[&>option]:bg-zinc-900" @change="filterBy('hours', ($event.target as HTMLSelectElement).value)">
            <option :value="24">最近 24 小时</option>
            <option :value="168">最近 7 天</option>
            <option :value="720">最近 30 天</option>
          </select>
        </div>

        <button v-if="pendingNew" type="button" class="mb-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-teal-600/10 text-sm text-teal-800 hover:bg-teal-600/20 dark:text-teal-200" @click="reload">
          <Sparkles :size="15" aria-hidden="true" /> 有新消息，点这里看看
        </button>
        <div v-if="error" role="alert" class="mb-3 rounded-xl border border-amber-500/25 bg-amber-500/5 p-4 text-sm text-amber-800 dark:text-amber-200">
          快讯暂时加载失败。<button type="button" class="ml-1 underline underline-offset-4" @click="reload">重新加载</button>
        </div>
        <div v-if="status === 'pending' && !data" role="status" class="panel py-16 text-center text-sm text-slate-500">正在整理快讯…</div>
        <div v-else-if="data?.items.length" class="panel overflow-hidden" :aria-busy="status === 'pending'">
          <article v-for="item in data.items" :key="item.id" class="border-b border-slate-900/7 px-4 py-5 last:border-0 sm:px-6 sm:py-6 dark:border-white/8">
            <div class="mb-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-xs">
              <span class="rounded-md px-2 py-0.5 font-medium" :class="item.kind === 'rumor' ? 'bg-amber-500/10 text-amber-800 dark:text-amber-300' : 'bg-teal-500/10 text-teal-800 dark:text-teal-300'">{{ item.kind === 'rumor' ? '传闻线索' : '报道' }}</span>
              <span class="text-slate-500 dark:text-zinc-400">二手消息 · 待核实</span>
              <time :datetime="item.published_at || undefined" class="text-slate-500 sm:ml-auto dark:text-zinc-400" :title="`${dateLabel(item.published_at)}（北京时间），视频发布时间`">视频发布 {{ dateLabel(item.published_at) }}</time>
            </div>
            <NuxtLink :to="{ path: `/contents/${item.id}`, query: { community: 'ai' } }" class="block rounded-sm text-[17px] font-semibold leading-relaxed text-slate-900 hover:text-teal-700 sm:text-[19px] dark:text-zinc-100 dark:hover:text-teal-300">
              <h2>{{ item.headline }}</h2>
            </NuxtLink>
            <p class="mt-2 text-sm leading-7 text-slate-600 dark:text-zinc-300">{{ item.summary }}</p>
            <div class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
              <span class="text-slate-500 dark:text-zinc-400">{{ item.platform === 'bilibili' ? 'B 站' : item.platform }} · {{ item.source_name }}</span>
              <span v-for="topic in item.topics" :key="topic" class="text-teal-700 dark:text-teal-300">#{{ topic }}</span>
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-8 items-center gap-1 text-slate-600 hover:text-teal-700 sm:ml-auto dark:text-zinc-300 dark:hover:text-teal-300">看来源 <ArrowUpRight :size="13" aria-hidden="true" /></a>
            </div>
            <details class="mt-2 text-xs text-slate-500 dark:text-zinc-400">
              <summary class="w-fit cursor-pointer py-1.5 hover:text-teal-700 dark:hover:text-teal-300">原始标题与参考链接</summary>
              <p class="mt-1 leading-6">{{ item.original_title }}</p>
              <div v-if="item.references.length" class="mt-2 flex flex-wrap gap-3">
                <a v-for="link in item.references" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer" class="break-all underline underline-offset-4">{{ link.label }} ↗</a>
              </div>
              <p class="mt-1 leading-6">{{ item.references.length ? '以上为来源附带的链接，尚未逐条核对。' : '来源简介未附可追溯的一手链接。' }}</p>
            </details>
          </article>
        </div>
        <div v-else-if="!error" class="panel px-5 py-14 text-center">
          <Radio :size="26" class="mx-auto mb-4 text-teal-600 dark:text-teal-400" aria-hidden="true" />
          <h2 class="text-base font-medium">{{ hasFilters ? '还没有匹配的快讯' : '这段时间还没有筛选完成的快讯' }}</h2>
          <p class="mx-auto mt-2 max-w-sm text-sm leading-6 text-slate-500 dark:text-zinc-400">{{ hasFilters ? '试试其他关键词，或放宽时间范围。' : '新消息会在整理后出现在这里。你也可以切换到最近 30 天。' }}</p>
          <button v-if="hasFilters" type="button" class="mt-4 min-h-10 text-sm text-teal-700 underline underline-offset-4 dark:text-teal-300" @click="clearFilters">清除筛选</button>
        </div>
        <footer v-if="data?.total" class="mt-4 flex items-center justify-between gap-2 px-1 text-xs text-slate-500 dark:text-zinc-400">
          <span>{{ data.total }} 条快讯，按发布时间排序</span>
          <div v-if="totalPages > 1" class="flex items-center gap-3">
            <button type="button" :disabled="page <= 1" class="min-h-10 hover:text-teal-700" @click="router.replace({ query: { ...route.query, page: page - 1 } })">上一页</button>
            <span>{{ page }} / {{ totalPages }}</span>
            <button type="button" :disabled="page >= totalPages" class="min-h-10 hover:text-teal-700" @click="router.replace({ query: { ...route.query, page: page + 1 } })">下一页</button>
          </div>
        </footer>
      </section>

      <aside class="space-y-6 lg:sticky lg:top-20" aria-label="快讯来源说明">
        <section class="rounded-2xl bg-teal-700/5 p-5 dark:bg-teal-400/5">
          <h2 class="flex items-center gap-2 text-sm font-semibold text-teal-900 dark:text-teal-200"><Sparkles :size="16" aria-hidden="true" /> 小情报站的约定</h2>
          <ul class="mt-4 space-y-3 text-sm leading-6 text-slate-600 dark:text-zinc-300">
            <li class="flex gap-2"><Check :size="14" class="mt-1.5 shrink-0 text-teal-600" aria-hidden="true" />新消息按发布时间排，不按播放量排。</li>
            <li class="flex gap-2"><Check :size="14" class="mt-1.5 shrink-0 text-teal-600" aria-hidden="true" />报道与传闻分开，来源随手可查。</li>
            <li class="flex gap-2"><Check :size="14" class="mt-1.5 shrink-0 text-teal-600" aria-hidden="true" />暂未核实的内容，就坦白标出来。</li>
          </ul>
          <p class="mt-4 border-t border-teal-700/10 pt-4 text-xs leading-6 text-slate-500 dark:text-zinc-400">摘要依据视频标题与简介整理，未核对完整视频。这里的时间是视频发布时间，不是事件发生时间。</p>
        </section>
        <section class="px-1">
          <h2 class="text-sm font-semibold">消息从哪里来</h2>
          <p class="mt-2 text-xs leading-6 text-slate-500 dark:text-zinc-400">当前由 B 站快讯补充，X 原帖仍在验证接入。不会把二手转述标成官方确认。</p>
          <ul v-if="data?.sources.length" class="mt-3 space-y-3">
            <li v-for="source in data.sources" :key="`${source.platform}-${source.name}`" class="flex items-center justify-between gap-2 text-xs">
              <span class="min-w-0 truncate text-slate-600 dark:text-zinc-300">{{ source.name }}</span>
              <span class="shrink-0" :class="source.status === 'active' ? 'text-teal-700 dark:text-teal-300' : 'text-amber-700 dark:text-amber-300'">{{ sourceLabels[source.status] || '等待更新' }}</span>
            </li>
          </ul>
          <p v-else-if="data" class="mt-3 text-xs text-slate-500">还没有启用的资讯来源。</p>
          <p v-if="sourceTime" class="mt-4 text-xs leading-6 text-slate-500 dark:text-zinc-400">最近成功检查：{{ dateLabel(sourceTime) }}（北京时间）</p>
          <NuxtLink to="/sources" class="mt-3 inline-flex min-h-9 items-center gap-1 text-xs text-teal-700 hover:underline dark:text-teal-300">查看来源状态 <ArrowUpRight :size="13" aria-hidden="true" /></NuxtLink>
        </section>
      </aside>
    </div>
  </div>
</template>
