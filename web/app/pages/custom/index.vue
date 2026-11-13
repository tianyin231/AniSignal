<script setup lang="ts">
import { ChevronLeft, ChevronRight, Pause, Play, Plus, Radar, RefreshCw } from 'lucide-vue-next'
import type { CustomFeedRow, CustomQuota } from '~/composables/useAccountCenter'
import { apiErrorDetail } from '~/utils/api-errors'
import { customFeedBadge, customKindLabel } from '~/utils/custom-feeds'
import { loginRoute } from '~/utils/safe-redirect'
import { relativeTime, toTimestamp } from '~/utils/schedule'

useSeoMeta({ title: '定制监控 · AniSignal', description: '自由监控任意感兴趣的内容：B 站关键词、UP 主、RSS/Atom 订阅与 YouTube 频道，随时启停管理。' })

interface CustomFeedCard {
  id: number
  name: string
  kind: string
  platform: string
  target_value: string
  target_label: string
  description: string
  item_count: number
  owner_name: string | null
  site_managed: boolean
  latest_at: string | null
  latest_items?: { title: string | null, url: string | null, published_at: string | null }[]
}

const { api } = usePublicApi()
const route = useRoute()
const router = useRouter()
const pageSize = 30
// 后端公开列表的分页上界是 10000（MAX_PAGE_OFFSET），折算成页数后前后端一致
const MAX_PAGE = Math.floor(10_000 / pageSize) + 1
const page = computed(() => Math.min(MAX_PAGE, Math.max(1, Number(route.query.page) || 1)))
const now = useState('custom-reference-time', () => Date.now())

const { data, status, error, refresh } = await useLazyAsyncData(
  () => `custom-feeds-${page.value}`,
  (_app, { signal }) => api<{ items: CustomFeedCard[], total: number }>('/api/custom/feeds', { signal, query: { page: page.value, page_size: pageSize } }),
  { watch: [page] },
)
const items = computed(() => (data.value?.items ?? []).map((feed) => {
  const latest = toTimestamp(feed.latest_at)
  return {
    ...feed,
    badge: customFeedBadge(feed.platform),
    kindLabel: customKindLabel(feed.kind),
    updated: Number.isFinite(latest) ? `${relativeTime(latest, now.value)}更新` : '等待首次采集',
    previews: (feed.latest_items ?? []).map(item => ({ ...item, time: relativeTime(toTimestamp(item.published_at), now.value) })),
  }
}))
const totalPages = computed(() => Math.min(MAX_PAGE, Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize))))
const itemTotal = computed(() => (data.value?.items ?? []).reduce((sum, feed) => sum + feed.item_count, 0))
const lastUpdate = computed(() => {
  const latest = Math.max(...(data.value?.items ?? []).map(feed => toTimestamp(feed.latest_at)).filter(Number.isFinite))
  return Number.isFinite(latest) ? relativeTime(latest, now.value) : '—'
})
const goPage = (p: number) => router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })

// 侧栏"我的监控"：登录后读取自己的监控（含私有）
const { user, loaded } = useAuth()
const mine = ref<CustomFeedRow[] | null>(null)
const quota = ref<CustomQuota | null>(null)
watch(user, async (value) => {
  if (!value) {
    mine.value = quota.value = null
    return
  }
  try {
    [mine.value, quota.value] = await Promise.all([
      $fetch<CustomFeedRow[]>('/api/account/custom-feeds'),
      $fetch<CustomQuota>('/api/account/custom-quota'),
    ])
  } catch {
    mine.value = []
  }
}, { immediate: import.meta.client })
const manageTo = '/account?tab=custom'
const createTo = '/account?tab=custom&create=1'
const mineBusyId = ref<number | null>(null)
const mineNotice = ref<{ tone: 'ok' | 'error'; text: string } | null>(null)

async function toggleMine(row: CustomFeedRow) {
  if (mineBusyId.value || row.status !== 'active') return
  mineBusyId.value = row.id
  mineNotice.value = null
  try {
    await $fetch(`/api/account/custom-feeds/${row.id}`, { method: 'PATCH', body: { enabled: !row.enabled } })
    row.enabled = !row.enabled
    mineNotice.value = { tone: 'ok', text: row.enabled ? `已恢复「${row.name}」` : `已暂停「${row.name}」` }
  } catch (exc) {
    mineNotice.value = { tone: 'error', text: apiErrorDetail(exc, '状态切换失败') }
  } finally {
    mineBusyId.value = null
  }
}

async function refreshMine(row: CustomFeedRow) {
  if (mineBusyId.value || !row.enabled || row.status !== 'active') return
  mineBusyId.value = row.id
  mineNotice.value = null
  try {
    await $fetch(`/api/account/custom-feeds/${row.id}/collect`, { method: 'POST' })
    mineNotice.value = { tone: 'ok', text: `「${row.name}」已加入采集队列` }
  } catch (exc) {
    mineNotice.value = { tone: 'error', text: apiErrorDetail(exc, '手动刷新失败') }
  } finally {
    mineBusyId.value = null
  }
}

const pill = 'panel flex flex-col gap-0.5 rounded-xl px-3 py-2.5 sm:flex-row sm:items-baseline sm:gap-1.5 sm:px-4'
</script>

<template>
  <div>
    <section class="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div class="wall-heading">
        <p class="mono mb-1.5 text-xs tracking-wide text-muted">自建任意关键词 · UP 主 · RSS 订阅 · YouTube 频道 · 随时管理</p>
        <h1 class="text-[28px] font-bold leading-tight text-ink sm:text-[32px]">定制监控</h1>
      </div>
      <div class="grid w-full grid-cols-3 gap-2 sm:flex sm:w-auto sm:gap-2.5">
        <div :class="pill">
          <span class="num text-xl font-bold text-ink sm:text-[22px]">{{ data?.total ?? '—' }}</span>
          <span class="text-xs text-muted sm:text-[13px]">个公开监控</span>
        </div>
        <div :class="pill">
          <span class="num text-xl font-bold text-ink sm:text-[22px]">{{ data ? itemTotal.toLocaleString('zh-CN') : '—' }}</span>
          <span class="text-xs text-muted sm:text-[13px]">条已收录</span>
        </div>
        <div :class="pill">
          <span class="text-base font-bold text-ink sm:text-[17px]">{{ data ? lastUpdate : '—' }}</span>
          <span class="text-xs text-muted sm:text-[13px]">最近更新</span>
        </div>
      </div>
    </section>

    <div class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div class="min-w-0">
        <div v-if="status === 'pending' && !data" class="grid gap-4 sm:grid-cols-2" aria-hidden="true">
          <div v-for="i in 4" :key="i" class="panel h-56 animate-pulse" />
        </div>
        <UiNotice v-else-if="error" tone="warn">
          监控列表暂时加载失败。<button type="button" class="ml-1 font-semibold underline" @click="refresh()">重新加载</button>
        </UiNotice>
        <div v-else-if="items.length" class="grid gap-4 sm:grid-cols-2">
          <NuxtLink v-for="feed in items" :key="feed.id" :to="`/custom/${feed.id}`" class="panel group flex flex-col p-5 transition-colors hover:border-faint/60">
            <div class="flex items-center gap-3">
              <span class="grid size-10 shrink-0 place-items-center rounded-xl text-[13px] font-bold text-white" :class="feed.badge.badge" :title="feed.badge.label">{{ feed.badge.short }}</span>
              <div class="min-w-0 flex-1">
                <h2 class="truncate text-base font-bold text-ink group-hover:text-brand-ink">{{ feed.name }}</h2>
                <p class="truncate text-xs text-muted">{{ feed.kindLabel }} · {{ feed.target_label || feed.target_value }}</p>
              </div>
            </div>
            <p v-if="feed.description" class="mt-3 line-clamp-2 text-[13px] leading-relaxed text-muted">{{ feed.description }}</p>
            <ul v-if="feed.previews.length" class="mt-3 space-y-1.5 border-t border-line-soft pt-3" aria-label="最新内容">
              <li v-for="(item, index) in feed.previews" :key="index" class="flex items-baseline gap-2 text-[13px]">
                <span class="line-clamp-1 min-w-0 flex-1 text-ink-2">{{ item.title || '（无标题）' }}</span>
                <span class="mono shrink-0 text-[11px] text-faint">{{ item.time }}</span>
              </li>
            </ul>
            <footer class="mt-auto flex items-center gap-1.5 pt-4 text-xs text-muted">
              <span><span class="num text-sm font-bold text-ink">{{ feed.item_count }}</span> 条</span>
              <span>· {{ feed.updated }}</span>
              <span class="ml-auto truncate" :class="feed.site_managed ? 'font-medium text-brand-ink' : ''">{{ feed.site_managed ? '站点维护' : feed.owner_name || '匿名用户' }}</span>
            </footer>
          </NuxtLink>
        </div>
        <div v-else class="rounded-2xl border border-dashed border-line px-5 py-14 text-center">
          <p class="text-sm text-muted">还没有公开的定制监控。</p>
          <p class="mt-1 text-xs text-faint">在账号中心创建监控并选择"公开到定制化频道"，就会出现在这里。</p>
        </div>

        <nav v-if="totalPages > 1" aria-label="分页" class="mt-5 flex items-center justify-between text-xs text-muted">
          <span>共 {{ data?.total }} 个公开监控</span>
          <div class="flex items-center gap-1">
            <button type="button" class="grid size-8 place-items-center rounded-full hover:bg-chip" :disabled="page <= 1" aria-label="上一页" @click="goPage(page - 1)"><ChevronLeft :size="16" /></button>
            <span class="mono min-w-14 text-center">{{ page }} / {{ totalPages }}</span>
            <button type="button" class="grid size-8 place-items-center rounded-full hover:bg-chip" :disabled="page >= totalPages" aria-label="下一页" @click="goPage(page + 1)"><ChevronRight :size="16" /></button>
          </div>
        </nav>
      </div>

      <aside class="space-y-4 lg:sticky lg:top-[88px]">
        <section class="panel p-5" aria-labelledby="mine-title">
          <header class="flex items-center gap-3">
            <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-arknights-soft text-arknights-ink" aria-hidden="true"><Radar :size="19" /></span>
            <div class="min-w-0 flex-1">
              <h2 id="mine-title" class="text-base font-bold text-ink">我的监控源</h2>
              <p v-if="quota" class="text-xs text-muted">{{ quota.admitted ? `已用 ${quota.used}/${quota.max_feeds} 个` : '尚未开通公测名额' }}</p>
              <p v-else class="text-xs text-muted">支持关键词 / UP 主 / RSS / YouTube</p>
            </div>
          </header>

          <template v-if="loaded && user">
            <p v-if="mineNotice" class="mt-3 rounded-lg px-2.5 py-1.5 text-xs" :class="mineNotice.tone === 'ok' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' : 'bg-red-500/10 text-red-600 dark:text-red-400'">
              {{ mineNotice.text }}
            </p>
            <ul v-if="mine?.length" class="mt-4 divide-y divide-line-soft border-y border-line-soft">
              <li v-for="row in mine.slice(0, 6)" :key="row.id" class="flex items-center gap-1.5 py-2 text-sm">
                <NuxtLink :to="`/custom/${row.id}`" class="group flex min-w-0 flex-1 items-center gap-2">
                  <span class="size-1.5 shrink-0 rounded-full" :class="row.status !== 'active' ? 'bg-hot' : row.enabled ? 'bg-ok' : 'bg-faint'" aria-hidden="true" />
                  <span class="min-w-0 flex-1 truncate text-ink-2 group-hover:text-brand-ink">{{ row.name }}</span>
                  <span v-if="!row.is_public" class="shrink-0 rounded bg-chip px-1.5 text-[11px] text-muted">私有</span>
                  <span class="num shrink-0 text-xs text-faint">{{ row.item_count }}</span>
                </NuxtLink>
                <button
                  type="button"
                  class="grid size-7 shrink-0 place-items-center rounded-md text-muted transition-colors hover:bg-chip hover:text-ink disabled:opacity-40"
                  :disabled="mineBusyId === row.id || !row.enabled || row.status !== 'active'"
                  :title="`立即刷新：${row.name}`"
                  :aria-label="`立即刷新：${row.name}`"
                  @click="refreshMine(row)"
                >
                  <RefreshCw :size="13" />
                </button>
                <button
                  type="button"
                  class="grid size-7 shrink-0 place-items-center rounded-md text-muted transition-colors hover:bg-chip hover:text-ink disabled:opacity-40"
                  :disabled="mineBusyId === row.id || row.status !== 'active'"
                  :title="`${row.enabled ? '暂停' : '恢复'}：${row.name}`"
                  :aria-label="`${row.enabled ? '暂停' : '恢复'}：${row.name}`"
                  @click="toggleMine(row)"
                >
                  <Pause v-if="row.enabled" :size="13" /><Play v-else :size="13" />
                </button>
              </li>
            </ul>
            <p v-else-if="mine" class="mt-4 text-sm text-muted">还没有创建监控源。</p>
            <div class="mt-4 flex gap-2">
              <NuxtLink :to="createTo" class="btn-primary inline-flex h-9 flex-1 items-center justify-center gap-1 text-sm">
                <Plus :size="15" />新建监控
              </NuxtLink>
              <NuxtLink v-if="mine?.length" :to="manageTo" class="inline-flex h-9 items-center justify-center rounded-[10px] border border-line px-3 text-sm text-ink-2 transition-colors hover:bg-chip hover:text-ink">
                管理全部
              </NuxtLink>
            </div>
          </template>
          <template v-else>
            <ul class="mt-4 space-y-2 text-[13px] text-ink-2">
              <li>· 自建任意关键词、UP 主、RSS 订阅或 YouTube 频道</li>
              <li>· 随时一键启停、立即抓取、设置包含词与排除词</li>
              <li>· 可设为私有独享，或公开到这里与大家共享</li>
            </ul>
            <NuxtLink :to="loginRoute(createTo)" class="btn-primary mt-4 inline-flex h-9 w-full items-center justify-center">登录后创建监控</NuxtLink>
          </template>
        </section>
        <p class="px-1 text-xs leading-relaxed text-faint">标"站点维护"的监控由 AniSignal 维护，保留原始发布时间与原文链接。</p>
      </aside>
    </div>
  </div>
</template>
