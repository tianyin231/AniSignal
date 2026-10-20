<script setup lang="ts">
import { ArrowUpRight, Flame, RefreshCw, Search } from 'lucide-vue-next'
import { fmtTime } from '~/utils/admin-types'
import { platformMeta, type Platform } from '~/utils/data'
import { imageSrcset, imageUrl } from '~/utils/image'
import { beijingParts } from '~/utils/schedule'

interface NewsItem {
  id: number | string
  path?: string
  image?: string | null
  engine?: string
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
  story_id: number | null
  report_count: number
  participant_count: number
  heat: number
  selected: boolean
  score: number | null
  latest_at: string
  updated_at: string
  sources: NewsItem[]
  developments: (NewsItem & { event_id: number | null })[]
}
interface NewsList {
  engine?: string
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
  sort: route.query.mode === 'hot' ? 'hot' : 'time',
  selected: route.query.mode === 'selected',
}))
const request = computed(() => ({ ...filters.value, limit: 20, offset: (page.value - 1) * 20 }))
const { data, status, error, refresh } = await useLazyAsyncData(
  () => `ai-news-${JSON.stringify(request.value)}`,
  (_app, { signal }) => api<NewsList>('/api/ai-news/events', { query: request.value, signal }),
)
const { data: edition } = await useLazyAsyncData('ai-news-edition',
  (_app, { signal }) => api<{ day: string | null; items: NewsItem[] }>('/api/ai-news/edition', { signal }))
const { data: pulse } = await useLazyAsyncData('ai-news-pulse', async (_app, { signal }) => {
  const [day, week] = await Promise.all([
    api<NewsList>('/api/ai-news/events', { query: { hours: 24, limit: 1 }, signal }),
    api<NewsList>('/api/ai-news/events', { query: { hours: 168, limit: 1 }, signal }),
  ])
  return { day: day.total, week: week.total }
})
const { data: coverList } = await useLazyAsyncData(
  'ai-news-covers',
  (_app, { signal }) => api<{ items: { id: number, cover: string | null }[] }>('/api/contents', {
    query: { community: 'ai', limit: 100 },
    signal,
  }),
)
// 快讯接口没有封面。代表内容的 id 与内容库相同，头图再补一次详情里的首张图。
const { data: leadShot } = await useLazyAsyncData(
  () => `ai-lead-media-${page.value === 1 ? (data.value?.items[0]?.id ?? 0) : 0}`,
  async (_app, { signal }) => {
    const id = page.value === 1 ? data.value?.items[0]?.id : 0
    if (!id || typeof id === 'string') return { id: 0, url: '' }
    try {
      const detail = await api<{ media?: string[] }>(`/api/contents/${id}`, { signal })
      return { id, url: detail.media?.[0] || '' }
    } catch {
      return { id, url: '' }
    }
  },
)
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total || 0) / 20)))
const hasFilters = computed(() => !!(filters.value.q || filters.value.topic || filters.value.kind !== 'all' || filters.value.selected || filters.value.sort === 'hot'))
const pendingNew = ref(false)
const refreshBusy = ref(false)
const knownTopics = ['OpenAI', 'Codex', 'Claude', 'Gemini', 'DeepSeek', '开源模型', '行业动态']
const kinds = [{ key: 'all', label: '全部快讯' }, { key: 'report', label: '报道' }, { key: 'rumor', label: '传闻线索' }]
const modes = [{ key: 'time', label: '最新' }, { key: 'hot', label: '热点' }, { key: 'selected', label: '精选' }]
const sourceLabels: Record<string, string> = { delayed: '更新延迟', pending: '等待首轮' }

const now = useState('ai-news-now', () => Date.now())
const dateLine = computed(() => {
  const p = beijingParts(now.value)
  return `${p.year}.${String(p.month).padStart(2, '0')}.${String(p.day).padStart(2, '0')} ${p.weekday} · ${p.hm} 更新`
})
const covers = computed(() => {
  const map = new Map<number, string>()
  for (const item of coverList.value?.items ?? []) {
    if (item.cover) map.set(Number(item.id), item.cover)
  }
  return map
})
const lead = computed(() => (page.value === 1 ? data.value?.items[0] : undefined))
const feedItems = computed(() => {
  const items = data.value?.items ?? []
  return lead.value ? items.slice(1) : items
})
const sourceCells = computed(() => [...(data.value?.sources ?? [])].sort((a, b) => Number(a.status !== 'active') - Number(b.status !== 'active')))
const sourcesOk = computed(() => sourceCells.value.filter(s => s.status === 'active').length)
const delayedSources = computed(() => sourceCells.value.filter(s => s.status !== 'active'))
const leadCover = computed(() => {
  if (!lead.value) return ''
  if (leadShot.value?.id === lead.value.id && leadShot.value.url) return leadShot.value.url
  return lead.value.image || covers.value.get(Number(lead.value.id)) || ''
})
const leadCoverFailed = ref(false)
watch(leadCover, () => { leadCoverFailed.value = false })
const failedCovers = ref<(number | string)[]>([])
const feedTitle = computed(() => (filters.value.selected ? '精选' : filters.value.sort === 'hot' ? '热点' : '最新'))

function newsLink(item: NewsItem) {
  return item.path || `/contents/${item.id}?community=ai`
}
function platformMark(platform: string) {
  if (platform === 'rss') return { label: '新闻源', short: 'RSS', badge: 'bg-teal-700' }

  return platformMeta[platform as Platform] ?? { label: platform, short: platform.slice(0, 1).toUpperCase(), badge: 'bg-slate-500' }
}
function rowCover(id: number | string) {
  if (failedCovers.value.includes(id)) return ''
  return data.value?.items.find(i => i.id === id)?.image || covers.value.get(Number(id)) || ''
}
function markCoverFailed(id: number | string) {
  if (!failedCovers.value.includes(id)) failedCovers.value = [...failedCovers.value, id]
}
function showOriginal(item: NewsItem) {
  return (item.references?.length || 0) > 0
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
let probeController: AbortController | undefined
async function checkUpdates() {
  if (document.visibilityState !== 'visible' || pendingNew.value || page.value !== 1 || refreshBusy.value || status.value !== 'success' || probeController) return
  const controller = new AbortController()
  probeController = controller
  try {
    const head = await api<NewsList>('/api/ai-news/events', { query: { ...filters.value, limit: 1 }, signal: controller.signal })
    const stamp = (item?: NewsItem) => item ? `${item.id}:${item.report_count}:${item.latest_at}:${item.updated_at}` : ''
    if (!controller.signal.aborted && head.items[0] && stamp(head.items[0]) !== stamp(data.value?.items[0])) pendingNew.value = true
  } catch { /* 保留正在阅读的列表，用户手动刷新时显示错误。 */ }
  finally { if (probeController === controller) probeController = undefined }
}
let timer: ReturnType<typeof setInterval> | undefined
watch(() => route.query.q, value => { search.value = String(value || '') })
watch(request, () => { probeController?.abort(); pendingNew.value = false })
onMounted(() => {
  now.value = Date.now()
  timer = setInterval(() => {
    now.value = Date.now()
    void checkUpdates()
  }, 60_000)
  document.addEventListener('visibilitychange', checkUpdates)
})
onBeforeUnmount(() => {
  probeController?.abort()
  if (timer) clearInterval(timer)
  document.removeEventListener('visibilitychange', checkUpdates)
})
</script>

<template>
  <div class="min-w-0 max-w-full overflow-x-clip">
    <section
      class="dark ch-ai relative isolate mb-5 w-full min-w-0 max-w-full overflow-hidden rounded-[20px] bg-[#0c0d11] text-ink shadow-card"
      :aria-label="lead?.headline || 'AI 快讯'"
    >
      <div v-if="leadCover && !leadCoverFailed" class="relative w-full min-w-0 max-w-full overflow-hidden sm:absolute sm:inset-y-0 sm:right-0 sm:-z-10 sm:w-[46%]">
        <img
          :src="imageUrl(leadCover, 1600)"
          :srcset="imageSrcset(leadCover, [640, 960, 1600])"
          sizes="(min-width: 640px) 46vw, 100vw"
          alt=""
          decoding="async"
          fetchpriority="high"
          referrerpolicy="no-referrer"
          class="aspect-[16/9] w-full max-w-full object-cover sm:aspect-auto sm:h-full"
          @error="leadCoverFailed = true"
        >
        <div class="absolute inset-0 bg-[#0c0d11]/30 sm:hidden" aria-hidden="true" />
        <div class="absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-[#0c0d11] to-transparent sm:hidden" aria-hidden="true" />
        <div class="absolute inset-y-0 left-0 hidden w-16 bg-gradient-to-r from-[#0c0d11] to-transparent sm:block" aria-hidden="true" />
      </div>
      <div
        v-else
        class="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(70%_90%_at_100%_0%,rgb(44_192_172/0.42),transparent_58%)]"
        aria-hidden="true"
      />
      <div
        class="relative flex w-full min-w-0 flex-col gap-3 px-5 py-6 max-sm:pr-10 sm:min-h-[340px] sm:justify-center sm:px-9 sm:py-10"
        :class="leadCover && !leadCoverFailed ? 'sm:w-[54%]' : 'sm:max-w-3xl'"
      >
        <p class="mono text-xs tracking-wide text-white/55">{{ dateLine }}</p>
        <p class="num text-xs font-medium uppercase tracking-[0.28em] text-ai-ink">AI News</p>
        <h1 v-if="lead" class="min-w-0 max-w-full text-[26px] font-extrabold leading-[1.18] text-white sm:text-[34px]">
          <NuxtLink :to="newsLink(lead)" class="line-clamp-3 block max-w-full hover:text-white/85">{{ lead.headline }}</NuxtLink>
        </h1>
        <h1 v-else-if="status !== 'pending'" class="text-[40px] font-extrabold leading-none tracking-wide text-white sm:text-[56px]">AI 快讯</h1>
        <p v-if="lead?.summary" class="line-clamp-3 min-w-0 max-w-full text-[15px] leading-relaxed text-white/75">{{ lead.summary }}</p>
        <div class="mt-1 grid grid-cols-3 gap-3">
          <p class="min-w-0"><span class="num block text-2xl font-bold text-ai-ink sm:text-3xl">{{ pulse?.day ?? '—' }}</span><span class="mt-0.5 block text-[11px] leading-tight text-white/70 sm:text-[13px]">条 · 24h</span></p>
          <p class="min-w-0"><span class="num block text-2xl font-bold text-ai-ink sm:text-3xl">{{ pulse?.week ?? '—' }}</span><span class="mt-0.5 block text-[11px] leading-tight text-white/70 sm:text-[13px]">条 · 7 天</span></p>
          <p v-if="sourceCells.length" class="min-w-0"><span class="num block text-2xl font-bold text-ai-ink sm:text-3xl">{{ sourcesOk }}<span class="font-medium text-white/40">/{{ sourceCells.length }}</span></span><span class="mt-0.5 block text-[11px] leading-tight text-white/70 sm:text-[13px]">来源正常</span></p>
        </div>
        <div v-if="lead" class="mt-1 flex w-full min-w-0 max-w-full flex-wrap items-center gap-2">
          <span class="inline-flex h-7 items-center rounded-full px-2.5 text-xs font-semibold" :class="lead.kind === 'rumor' ? 'bg-warn/20 text-warn' : 'bg-white/10 text-white'">{{ lead.kind === 'rumor' ? '传闻线索' : '报道' }}</span>
          <span v-if="lead.participant_count >= 2" class="inline-flex h-7 items-center gap-1 rounded-full bg-white/10 px-2.5 text-xs text-white"><Flame :size="13" aria-hidden="true" />{{ lead.participant_count }} 个来源</span>
          <span class="max-w-[14rem] truncate text-[13px] text-white/65">{{ platformMark(lead.platform).label }} · {{ lead.source_name }}</span>
          <time :datetime="lead.published_at || undefined" class="mono text-[13px] text-white/55">{{ lead.published_at ? fmtTime(lead.published_at) : '时间未知' }}</time>
          <a
            v-if="lead.url"
            :href="lead.url"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex h-9 items-center gap-1.5 rounded-full border border-white/20 bg-white/5 px-3.5 text-[13px] text-white/90 transition-colors hover:border-ai hover:text-ai-ink"
          >
            原文 <ArrowUpRight :size="13" aria-hidden="true" />
          </a>
        </div>
      </div>
    </section>

    <nav class="flex flex-wrap gap-4 text-sm text-ink-2" aria-label="AI 栏目"><NuxtLink to="/ai/reports" class="hover:text-ink">日报 · 周报 · 月报</NuxtLink><a href="/api/ai-news/rss.xml" class="hover:text-ink">精选 RSS</a></nav>
    <div class="grid items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <section class="panel min-w-0 overflow-hidden" aria-labelledby="ai-feed-title" :aria-busy="status === 'pending'">
        <header class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3.5 sm:px-5">
          <h2 id="ai-feed-title" class="text-lg font-bold text-ink">{{ feedTitle }}</h2>
          <div class="flex items-center gap-1">
            <div class="flex gap-0.5 rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="快讯排序">
              <button
                v-for="mode in modes"
                :key="mode.key"
                type="button"
                role="tab"
                :aria-selected="(route.query.mode || 'time') === mode.key"
                class="h-8 rounded-lg px-3.5 text-[13px] transition-colors"
                :class="(route.query.mode || 'time') === mode.key ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
                @click="filterBy('mode', mode.key === 'time' ? undefined : mode.key)"
              >
                {{ mode.label }}
              </button>
            </div>
            <button
              type="button"
              :disabled="refreshBusy || status === 'pending'"
              class="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[13px] text-muted transition-colors hover:bg-chip hover:text-ink disabled:opacity-40"
              @click="reload"
            >
              <RefreshCw :size="14" :class="{ 'animate-spin motion-reduce:animate-none': refreshBusy }" aria-hidden="true" />
              {{ refreshBusy ? '刷新中…' : '刷新' }}
            </button>
          </div>
        </header>

        <div class="flex flex-col gap-2 border-b border-line px-4 py-3 sm:flex-row sm:items-center sm:px-5">
          <form class="flex min-w-0 flex-1 items-center gap-2" role="search" @submit.prevent="filterBy('q', search.trim())">
            <div class="relative min-w-0 flex-1">
              <Search :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
              <input v-model="search" maxlength="100" type="search" aria-label="搜索 AI 快讯" placeholder="模型、产品或作者" class="input h-10 w-full pl-9 text-[13px]">
            </div>
            <button type="submit" class="btn-primary h-10 shrink-0 px-4">搜索</button>
          </form>
          <select :value="filters.hours" aria-label="快讯时间范围" class="select h-10 w-full py-0 text-[13px] sm:w-auto" @change="filterBy('hours', ($event.target as HTMLSelectElement).value)">
            <option :value="24">最近 24 小时</option>
            <option :value="168">最近 7 天</option>
            <option :value="720">最近 30 天</option>
          </select>
        </div>

        <div class="flex gap-1.5 overflow-x-auto border-b border-line px-4 py-2.5 [scrollbar-width:none] sm:px-5">
          <button
            v-for="kind in data?.engine === 'aihot' ? [] : kinds"
            :key="kind.key"
            type="button"
            class="h-8 shrink-0 rounded-full px-3.5 text-[13px] transition-colors"
            :class="filters.kind === kind.key ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
            :aria-pressed="filters.kind === kind.key"
            @click="filterBy('kind', kind.key === 'all' ? undefined : kind.key)"
          >
            {{ kind.label }}
          </button>
          <span class="mx-0.5 w-px shrink-0 self-center bg-line" aria-hidden="true" />
          <button
            type="button"
            class="h-8 shrink-0 rounded-full px-3.5 text-[13px] transition-colors"
            :class="!filters.topic ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
            :aria-pressed="!filters.topic"
            @click="filterBy('topic', '')"
          >
            全部话题
          </button>
          <button
            v-for="topic in data?.topics || knownTopics"
            :key="topic"
            type="button"
            class="h-8 shrink-0 rounded-full px-3.5 text-[13px] transition-colors"
            :class="filters.topic === topic ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
            :aria-pressed="filters.topic === topic"
            @click="filterBy('topic', topic)"
          >
            {{ topic }}
          </button>
        </div>

        <button
          v-if="pendingNew"
          type="button"
          class="flex w-full items-center justify-center gap-2 border-b border-line bg-brand-soft py-2.5 text-xs font-semibold text-brand-ink"
          @click="reload"
        >
          <span class="size-1.5 animate-pulse rounded-full bg-brand motion-reduce:animate-none" aria-hidden="true" />
          有新内容
        </button>
        <div v-if="error" role="alert" class="border-b border-line px-4 py-3 text-sm text-amber-700 dark:text-amber-300 sm:px-5">
          快讯暂时加载失败。<button type="button" class="ml-1 underline" @click="reload">重新加载</button>
        </div>
        <div v-if="status === 'pending' && !data" role="status" class="px-4 py-16 text-center text-sm text-muted">加载中…</div>
        <div v-else :class="{ 'opacity-60 transition-opacity': status === 'pending' }">
          <article v-for="item in feedItems" :key="item.story_id ? `event:${item.story_id}` : `content:${item.id}`" class="border-b border-line-soft last:border-0">
            <div class="flex gap-3 px-4 py-3.5 transition-colors hover:bg-line-soft sm:gap-4 sm:px-5">
              <NuxtLink
                v-if="rowCover(item.id)"
                :to="newsLink(item)"
                class="relative aspect-video w-28 shrink-0 overflow-hidden rounded-[10px] bg-chip sm:w-40"
              >
                <img
                  :src="imageUrl(rowCover(item.id), 320)"
                  :srcset="imageSrcset(rowCover(item.id), [160, 320])"
                  sizes="(min-width: 640px) 160px, 112px"
                  alt=""
                  referrerpolicy="no-referrer"
                  loading="lazy"
                  decoding="async"
                  class="size-full object-cover"
                  @error="markCoverFailed(item.id)"
                >
                <span class="absolute bottom-1.5 left-1.5 hidden rounded-[5px] px-1.5 py-px text-[10px] font-bold leading-4 text-white sm:block" :class="platformMark(item.platform).badge">{{ platformMark(item.platform).label }}</span>
              </NuxtLink>
              <span v-else class="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white" :class="platformMark(item.platform).badge" aria-hidden="true">{{ platformMark(item.platform).short }}</span>
              <div class="min-w-0 flex-1">
                <NuxtLink :to="newsLink(item)" class="line-clamp-2 block text-[15px] font-semibold leading-normal text-ink hover:text-brand-ink">
                  {{ item.headline }}
                </NuxtLink>
                <p class="mt-1 text-[13px] leading-relaxed text-muted" :class="rowCover(item.id) ? 'line-clamp-1' : 'line-clamp-2'">{{ item.summary }}</p>
                <div class="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted">
                  <span class="inline-flex h-5 items-center rounded-md px-1.5 font-semibold" :class="item.kind === 'rumor' ? 'bg-warn/10 text-warn' : 'bg-ai-soft text-ai-ink'">{{ item.kind === 'rumor' ? '传闻线索' : '报道' }}</span>
                  <span v-if="item.participant_count >= 2" class="inline-flex items-center gap-1 text-hot"><Flame :size="13" aria-hidden="true" />{{ item.participant_count }} 个来源</span>
                  <span class="max-w-[16rem] truncate">{{ rowCover(item.id) ? item.source_name : `${platformMark(item.platform).label} · ${item.source_name}` }}</span>
                  <time :datetime="item.published_at || undefined" class="mono text-faint">{{ item.published_at ? fmtTime(item.published_at) : '时间未知' }}</time>
                  <span v-for="topic in item.topics" :key="topic" class="rounded-md bg-chip px-1.5 py-0.5 text-[11px] text-ink-2">{{ topic }}</span>
                  <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link ml-auto inline-flex min-h-8 items-center gap-1">原文 <ArrowUpRight :size="13" aria-hidden="true" /></a>
                </div>
              </div>
            </div>
            <details v-if="item.report_count > 1 || item.developments.length > 1" class="px-4 pb-3 sm:px-5">
              <summary class="min-h-9 cursor-pointer content-center text-[13px] text-ink-2 hover:text-ink">
                {{ item.report_count }} 条报道<template v-if="item.developments.length > 1"> · {{ item.developments.length }} 项进展</template>
              </summary>
              <ol class="mt-2 border-l border-line pl-4">
                <li v-for="development in item.developments" :key="development.event_id || development.id" class="pb-4">
                  <div class="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <time :datetime="development.published_at || undefined" class="mono text-faint">{{ development.published_at ? fmtTime(development.published_at) : '时间未知' }}</time>
                    <span v-if="development.kind === 'rumor'" class="text-warn">传闻线索</span>
                  </div>
                  <NuxtLink :to="newsLink(development)" class="text-sm font-semibold leading-6 text-ink hover:text-brand-ink">{{ development.headline }}</NuxtLink>
                  <p class="mt-1 line-clamp-2 text-[13px] leading-relaxed text-muted">{{ development.summary }}</p>
                  <div class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                    <a
                      v-for="source in development.sources"
                      :key="source.id"
                      :href="source.url || `/contents/${source.id}`"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="link inline-flex min-h-8 items-center gap-1 text-xs"
                    >{{ source.source_name }}<ArrowUpRight :size="12" aria-hidden="true" /></a>
                  </div>
                </li>
              </ol>
            </details>
            <details v-if="showOriginal(item)" class="px-4 pb-3 text-xs text-muted sm:px-5">
              <summary class="w-fit cursor-pointer py-1.5 hover:text-ink">参考链接</summary>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                <a v-for="link in item.references" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer" class="link break-all">{{ link.label }}</a>
              </div>
            </details>
          </article>
          <div v-if="data && !feedItems.length && !lead && !error" class="px-5 py-14 text-center">
            <h2 class="text-base font-medium text-ink">{{ hasFilters ? '没有匹配的快讯' : '这段时间还没有快讯' }}</h2>
            <button v-if="hasFilters" type="button" class="link mt-3 min-h-10 text-sm" @click="clearFilters">清除筛选</button>
            <button v-else-if="filters.hours !== 720" type="button" class="link mt-3 min-h-10 text-sm" @click="filterBy('hours', 720)">查看最近 30 天</button>
          </div>
        </div>
        <footer v-if="data?.total" class="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-xs text-muted sm:px-5">
          <span>共 <span class="num text-[13px] font-semibold text-ink-2">{{ data.total }}</span> 条</span>
          <div v-if="totalPages > 1" class="flex items-center gap-1">
            <button type="button" :disabled="page <= 1" class="h-9 rounded-[10px] px-3 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40" @click="router.replace({ query: { ...route.query, page: page - 1 } })">上一页</button>
            <span class="mono px-1.5">{{ page }} / {{ totalPages }}</span>
            <button type="button" :disabled="page >= totalPages" class="h-9 rounded-[10px] px-3 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40" @click="router.replace({ query: { ...route.query, page: page + 1 } })">下一页</button>
          </div>
        </footer>
      </section>

      <aside class="flex min-w-0 flex-col gap-5 lg:sticky lg:top-[88px]" aria-label="快讯侧栏">
        <section v-if="edition?.items.length" class="panel p-5">
          <header class="flex items-baseline justify-between gap-2">
            <h2 class="text-base font-bold text-ink">每日速报</h2>
            <time class="mono text-xs text-faint">{{ edition.day?.replaceAll('-', '/') }}</time>
          </header>
          <ol class="mt-3 space-y-2.5">
            <li v-for="(item, index) in edition.items" :key="item.id" class="flex gap-2 text-sm leading-6">
              <span class="num w-4 shrink-0 text-faint">{{ index + 1 }}</span>
              <NuxtLink :to="newsLink(item)" class="text-ink hover:text-brand-ink">{{ item.headline }}</NuxtLink>
            </li>
          </ol>
        </section>
        <section class="panel p-5">
          <header class="flex items-baseline justify-between">
            <h2 class="text-base font-bold text-ink">来源健康</h2>
            <span v-if="sourceCells.length" class="num text-[15px] font-bold text-ink">{{ sourcesOk }}<span class="font-medium text-faint">/{{ sourceCells.length }}</span></span>
          </header>
          <div
            v-if="sourceCells.length"
            class="mt-3.5 grid grid-cols-[repeat(15,minmax(0,1fr))] gap-[5px]"
            role="img"
            :aria-label="`${sourceCells.length} 个来源中 ${sourcesOk} 个正常`"
          >
            <span
              v-for="source in sourceCells"
              :key="`${source.platform}-${source.name}`"
              class="aspect-square rounded-[3px]"
              :class="source.status === 'active' ? 'bg-ok/75' : 'bg-warn'"
              :title="source.status === 'active' ? source.name : `${source.name} · ${sourceLabels[source.status] || '等待更新'}`"
            />
          </div>
          <ul v-if="delayedSources.length" class="mt-3 space-y-1.5">
            <li v-for="source in delayedSources" :key="`late-${source.platform}-${source.name}`" class="flex items-center justify-between gap-2 text-xs">
              <span class="min-w-0 truncate text-ink-2">{{ source.name }}</span>
              <span class="shrink-0 text-warn">{{ sourceLabels[source.status] || '等待更新' }}</span>
            </li>
          </ul>
          <p v-else-if="data && !sourceCells.length" class="mt-3 text-xs text-muted">还没有启用的资讯来源。</p>
          <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
            <NuxtLink to="/ai/sources" class="link text-[13px]">全部来源</NuxtLink>
            <a href="/api/ai-news/rss.xml" class="link inline-flex items-center gap-1 text-[13px]">订阅 RSS <ArrowUpRight :size="13" aria-hidden="true" /></a>
          </div>
        </section>
      </aside>
    </div>
  </div>
</template>
