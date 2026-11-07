<script setup lang="ts">
import { ArrowLeft, ChevronLeft, ChevronRight, ExternalLink, Settings2 } from 'lucide-vue-next'
import { customFeedBadge, customKindLabel } from '~/utils/custom-feeds'
import { imageSrcset, imageUrl } from '~/utils/image'
import { fmtShort, relativeTime, toTimestamp } from '~/utils/schedule'

interface CustomItem {
  id: number
  title: string | null
  url: string | null
  cover: string | null
  author_name: string | null
  view: number
  content_type: string
  platform: string
  published_at: string | null
}
interface CustomFeedDetail {
  id: number
  name: string
  kind: string
  platform: string
  target_value: string
  target_label: string
  description: string
  source_url: string | null
  min_views: number
  is_public: boolean
  owner_name: string | null
  site_managed: boolean
  community?: string | null
  item_count: number | null
  total: number
  enabled: boolean
  status: string
  is_owner: boolean
  items: CustomItem[]
}
interface FeedCard { id: number, name: string, platform: string, item_count: number, target_label: string, target_value: string }

const route = useRoute()
const router = useRouter()
const feedId = computed(() => Number(route.params.id))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30
const now = useState('custom-reference-time', () => Date.now())
const { api } = usePublicApi()
const forwardedHeaders = useRequestHeaders(['cookie'])
const { error, data, status, refresh } = await useLazyAsyncData(
  () => `custom-feed-${feedId.value}-${page.value}`,
  async (_app, { signal }) => {
    try {
      return await api<CustomFeedDetail>(`/api/custom/feeds/${feedId.value}`, {
        headers: forwardedHeaders, signal, query: { page: page.value, page_size: pageSize },
      })
    } catch (exc: any) {
      if (exc?.status === 404) return null
      throw exc
    }
  },
)
const { data: others } = useLazyAsyncData('custom-feeds-sidebar', (_app, { signal }) =>
  api<{ items: FeedCard[] }>('/api/custom/feeds', { signal, query: { page: 1, page_size: 8 } }))

if (data.value?.community) await navigateTo({ path: '/', query: { community: data.value.community } }, { redirectCode: 301 })
watch(() => data.value?.community, (community) => {
  if (community) navigateTo({ path: '/', query: { community } }, { replace: true })
})

useSeoMeta({ title: () => `${data.value?.name ?? '定制监控'} · 定制化 · AniSignal` })

const badge = computed(() => customFeedBadge(data.value?.platform ?? ''))
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))
const goPage = (value: number) => router.replace({ query: { ...route.query, page: value > 1 ? value : undefined } })
const PLATFORM_LABEL: Record<string, string> = { bilibili: 'B站', youtube: 'YouTube', x: 'X', game_official: '官网' }
const rows = computed(() => (data.value?.items ?? []).map((item) => {
  const ts = toTimestamp(item.published_at)
  return {
    ...item,
    platformLabel: PLATFORM_LABEL[item.platform] ?? item.platform,
    views: item.view ? `${item.view.toLocaleString('zh-CN')} ${item.content_type === 'notice' ? '阅读' : '播放'}` : '',
    time: Number.isFinite(ts) ? relativeTime(ts, now.value) : '',
    exactTime: Number.isFinite(ts) ? fmtShort(ts) : '',
  }
}))
const latest = computed(() => (page.value === 1 ? rows.value[0]?.time : '') || '')
const stateChip = computed(() => {
  if (!data.value) return null
  if (data.value.status !== 'active') return { label: '管理员已停用', tone: 'bg-hot/10 text-hot' }
  if (!data.value.enabled) return { label: '已暂停', tone: 'bg-chip text-muted' }
  return null
})
const sidebar = computed(() => (others.value?.items ?? []).filter(feed => feed.id !== feedId.value).slice(0, 6))
const emptyText = computed(() => {
  const d = data.value
  if (!d) return ''
  if (d.total) return '这一页没有内容，请返回上一页。'
  if (d.status !== 'active') return '该监控已被管理员停用。'
  return d.enabled ? '还没有抓到内容，监控会在后台持续抓取。' : '监控已暂停，恢复后会继续抓取。'
})
</script>

<template>
  <div>
    <NuxtLink to="/custom" class="-ml-1 mb-3 inline-flex h-8 items-center gap-1 rounded-lg px-1 text-sm text-muted transition-colors hover:text-ink">
      <ArrowLeft :size="15" /> 定制化
    </NuxtLink>

    <div v-if="status === 'pending' && !data" class="space-y-4" aria-hidden="true">
      <div class="h-20 w-2/3 animate-pulse rounded-2xl bg-chip" />
      <div class="panel h-96 animate-pulse" />
    </div>
    <section v-else-if="error || !data" class="panel px-5 py-14 text-center">
      <p class="text-sm text-muted">{{ error ? '监控暂时加载失败，请稍后刷新。' : '监控不存在，或者是私有监控，需要登录所属账号后查看。' }}</p>
      <div class="mt-4 flex justify-center gap-3 text-sm">
        <button v-if="error" type="button" class="btn-primary h-9 px-4" @click="refresh()">重新加载</button>
        <NuxtLink to="/custom" class="link inline-flex h-9 items-center">浏览公开监控</NuxtLink>
      </div>
    </section>

    <template v-else>
      <section class="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div class="wall-heading flex min-w-0 items-start gap-4">
          <span class="grid size-12 shrink-0 place-items-center rounded-2xl text-base font-bold text-white shadow-card" :class="badge.badge" :title="badge.label">{{ badge.short }}</span>
          <div class="min-w-0">
            <p class="mono text-xs tracking-wide text-muted">{{ customKindLabel(data.kind) }} · {{ data.target_label || data.target_value }}</p>
            <h1 class="mt-1 text-[24px] font-bold leading-tight text-ink sm:text-[28px]">{{ data.name }}</h1>
            <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
              <span class="rounded px-1.5 py-0.5 font-medium" :class="data.site_managed ? 'bg-brand-soft text-brand-ink' : 'bg-chip text-ink-2'">{{ data.site_managed ? '站点维护' : data.owner_name || '匿名用户' }}</span>
              <span v-if="!data.is_public" class="rounded bg-chip px-1.5 py-0.5 font-medium text-ink-2">私有 · 仅自己可见</span>
              <span v-if="stateChip" class="rounded px-1.5 py-0.5 font-medium" :class="stateChip.tone">{{ stateChip.label }}</span>
              <span><span class="num text-sm font-bold text-ink">{{ data.total.toLocaleString('zh-CN') }}</span> 条</span>
              <span v-if="latest">· 最新一条 {{ latest }}</span>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <a v-if="data.source_url" :href="data.source_url" target="_blank" rel="noopener" class="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-line bg-card px-3.5 text-sm font-medium text-ink-2 transition-colors hover:text-ink">
            <ExternalLink :size="14" /> 官方主页
          </a>
          <NuxtLink v-if="data.is_owner" to="/account?tab=custom" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5">
            <Settings2 :size="15" /> 管理监控
          </NuxtLink>
        </div>
      </section>

      <div class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section class="panel min-w-0 overflow-hidden" aria-label="监控内容">
          <article v-for="item in rows" :key="item.id" class="flex gap-4 border-b border-line-soft px-5 py-4 last:border-0">
            <a
              v-if="item.cover"
              :href="item.url ?? undefined"
              target="_blank"
              rel="noopener"
              tabindex="-1"
              aria-hidden="true"
              class="relative aspect-video w-28 shrink-0 self-start overflow-hidden rounded-lg bg-chip sm:w-40"
            >
              <img
                :src="imageUrl(item.cover, 320)"
                :srcset="imageSrcset(item.cover, [160, 320])"
                sizes="(min-width: 640px) 160px, 112px"
                alt=""
                referrerpolicy="no-referrer"
                loading="lazy"
                decoding="async"
                class="size-full object-cover"
              >
            </a>
            <div class="min-w-0 flex-1">
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="line-clamp-2 text-[15px] font-semibold leading-snug text-ink hover:text-brand-ink">
                {{ item.title || item.url }}
              </a>
              <p v-else class="line-clamp-2 text-[15px] font-semibold leading-snug text-ink">{{ item.title }}</p>
              <p class="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
                <span>{{ item.platformLabel }}</span>
                <span v-if="item.author_name">· {{ item.author_name }}</span>
                <span v-if="item.views">· {{ item.views }}</span>
                <span v-if="item.time" class="mono text-faint" :title="item.exactTime">· {{ item.time }}</span>
              </p>
            </div>
          </article>
          <p v-if="!rows.length" class="px-5 py-14 text-center text-sm text-muted">{{ emptyText }}</p>
          <footer v-if="totalPages > 1" class="flex items-center justify-between border-t border-line-soft px-5 py-3 text-xs text-muted">
            <span>共 {{ data.total }} 条 · 每页 {{ pageSize }} 条</span>
            <div class="flex items-center gap-1">
              <button type="button" class="grid size-8 place-items-center rounded-full hover:bg-chip" :disabled="page <= 1" aria-label="上一页" @click="goPage(page - 1)"><ChevronLeft :size="16" /></button>
              <span class="mono min-w-14 text-center">{{ page }} / {{ totalPages }}</span>
              <button type="button" class="grid size-8 place-items-center rounded-full hover:bg-chip" :disabled="page >= totalPages" aria-label="下一页" @click="goPage(page + 1)"><ChevronRight :size="16" /></button>
            </div>
          </footer>
        </section>

        <aside class="space-y-4 lg:sticky lg:top-[88px]">
          <section class="panel p-5" aria-labelledby="about-title">
            <h2 id="about-title" class="text-base font-bold text-ink">关于此监控</h2>
            <p v-if="data.description" class="mt-2 text-[13px] leading-relaxed text-muted">{{ data.description }}</p>
            <dl class="mt-3 divide-y divide-line-soft border-t border-line-soft text-[13px]">
              <div class="flex justify-between gap-3 py-2"><dt class="text-muted">类型</dt><dd class="text-ink-2">{{ customKindLabel(data.kind) }}</dd></div>
              <div class="flex justify-between gap-3 py-2"><dt class="text-muted">监控对象</dt><dd class="min-w-0 truncate text-ink-2">{{ data.target_label || data.target_value }}</dd></div>
              <div v-if="data.min_views > 0" class="flex justify-between gap-3 py-2"><dt class="text-muted">播放量门槛</dt><dd class="num text-ink-2">≥ {{ data.min_views.toLocaleString('zh-CN') }}</dd></div>
              <div class="flex justify-between gap-3 py-2"><dt class="text-muted">维护者</dt><dd class="text-ink-2">{{ data.site_managed ? 'AniSignal' : data.owner_name || '匿名用户' }}</dd></div>
            </dl>
          </section>
          <section v-if="sidebar.length" class="panel px-5 pb-2 pt-4" aria-labelledby="others-title">
            <header class="flex items-center justify-between">
              <h2 id="others-title" class="text-base font-bold text-ink">其他公开监控</h2>
              <NuxtLink to="/custom" class="link text-[13px]">全部</NuxtLink>
            </header>
            <ul class="mt-2">
              <li v-for="feed in sidebar" :key="feed.id" class="border-t border-line-soft">
                <NuxtLink :to="`/custom/${feed.id}`" class="group flex items-center gap-3 py-2.5">
                  <span class="grid size-7 shrink-0 place-items-center rounded-lg text-[10px] font-bold text-white" :class="customFeedBadge(feed.platform).badge">{{ customFeedBadge(feed.platform).short }}</span>
                  <span class="min-w-0 flex-1 truncate text-sm text-ink-2 group-hover:text-brand-ink">{{ feed.name }}</span>
                  <span class="num shrink-0 text-xs text-faint">{{ feed.item_count }}</span>
                </NuxtLink>
              </li>
            </ul>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>
