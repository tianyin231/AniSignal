<script setup lang="ts">
import { Activity, ArrowUpRight, Plus, RefreshCw } from 'lucide-vue-next'
import type { EChartsCoreOption } from 'echarts/core'
import { communityClasses, communityMeta, useCommunityScope } from '~/composables/useGameSection'
import type { PublicContentList, SteamPulseApp, SteamPulseResponse } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { toRawItem } from '~/utils/public-utils'

const RANGES = [
  { hours: 24, label: '24 小时' },
  { hours: 168, label: '7 天' },
  { hours: 720, label: '30 天' },
] as const

const route = useRoute()
const router = useRouter()
const theme = useState<'light' | 'dark'>('theme', () => 'light')
const { api } = usePublicApi()
const { scope, isAll } = useCommunityScope({ gameOnly: true })

const hours = computed(() => {
  const raw = Number(route.query.hours)
  return RANGES.some(r => r.hours === raw) ? raw : 168
})
const pageSize = 20
const MAX_PAGE = Math.floor(10_000 / pageSize) + 1
const page = computed(() => Math.min(MAX_PAGE, Math.max(1, Number(route.query.page) || 1)))
const rangeLabel = computed(() => RANGES.find(r => r.hours === hours.value)?.label ?? '7 天')
const currentMeta = computed(() => (isAll.value ? null : communityMeta(scope.value)))

useSeoMeta({
  title: () => (currentMeta.value ? `${currentMeta.value.label} · Steam · AniSignal` : 'Steam 追踪 · AniSignal'),
  description: '追踪游戏在 Steam 的实时在线人数、24 小时峰值、全区与简中好评率及官方公告更新。',
})

const pulseRequest = useLazyAsyncData(
  () => `steam-pulse-page-${scope.value}-${hours.value}`,
  (_app, { signal }) => api<SteamPulseResponse>('/api/steam/pulse', {
    query: { community: scope.value, hours: hours.value },
    signal,
  }),
  { watch: [scope, hours] },
)
const noticesRequest = useLazyAsyncData(
  () => `steam-notices-page-${scope.value}-${page.value}`,
  (_app, { signal }) => api<PublicContentList>('/api/contents', {
    query: {
      community: scope.value,
      platform: 'steam',
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    },
    signal,
  }),
  { watch: [scope, page] },
)
await Promise.all([pulseRequest, noticesRequest])

const { data: pulseData, status: pulseStatus, error: pulseError } = pulseRequest
const { data: noticesData, status: noticesStatus, error: noticesError } = noticesRequest

const apps = computed(() => pulseData.value?.apps ?? [])
const selectedAppId = ref<number | null>(apps.value[0]?.app_id ?? null)
watch(apps, (list) => {
  if (!list.length) {
    selectedAppId.value = null
    return
  }
  if (!list.some(item => item.app_id === selectedAppId.value)) {
    selectedAppId.value = list[0]!.app_id
  }
})
const activeApp = computed<SteamPulseApp | null>(
  () => apps.value.find(item => item.app_id === selectedAppId.value) ?? apps.value[0] ?? null,
)

const notices = computed(() => (noticesData.value?.items ?? []).map(toRawItem))
const totalNotices = computed(() => noticesData.value?.total ?? 0)
const totalPages = computed(() => Math.min(MAX_PAGE, Math.max(1, Math.ceil(totalNotices.value / pageSize))))

const refreshing = ref(false)
async function refreshAll() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.all([pulseRequest.refresh(), noticesRequest.refresh()])
  } finally {
    refreshing.value = false
  }
}

function setHours(next: number) {
  router.replace({ query: { ...route.query, hours: next === 168 ? undefined : String(next) } })
}
function goPage(next: number) {
  router.replace({ query: { ...route.query, page: next > 1 ? String(next) : undefined } })
}

function fmtCount(value: number | null): string {
  return value == null ? '—' : value.toLocaleString('zh-CN')
}
function fmtCompact(value: number): string {
  return new Intl.NumberFormat('zh-CN', {
    notation: value >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(value)
}
function reviewTotal(pos: number | null, neg: number | null): number | null {
  return pos != null && neg != null ? pos + neg : null
}
function rateTone(rate: number | null): string {
  if (rate == null) return 'text-ink'
  if (rate >= 70) return 'text-emerald-600 dark:text-emerald-400'
  if (rate >= 40) return 'text-amber-600 dark:text-amber-400'
  return 'text-hot'
}
function badgeTone(rate: number | null): string {
  if (rate == null) return 'bg-chip text-muted'
  if (rate >= 70) return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  if (rate >= 40) return 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  return 'bg-red-500/10 text-hot'
}
function gameBadge(community?: string | null) {
  const meta = communityMeta(community)
  if (!meta) return null
  return { label: meta.label, dot: communityClasses(meta.color).dot }
}

function fmtPointTime(value: string): string {
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const pick = (type: string) => parts.find(p => p.type === type)?.value ?? ''
  return `${pick('month')}/${pick('day')} ${pick('hour')}:${pick('minute')}`
}

function buildChartOption(app: SteamPulseApp): EChartsCoreOption {
  const labels = app.series.map(point => fmtPointTime(point.ts))
  const sameDay = new Set(labels.map(label => label.split(' ')[0])).size <= 1
  const muted = theme.value === 'dark' ? '#a1a1aa' : '#64748b'
  const splitLine = theme.value === 'dark' ? '#27272a' : '#e2e8f0'
  const accent = theme.value === 'dark' ? '#38bdf8' : '#0284c7'

  return {
    aria: { enabled: true, description: `${app.name} 近 ${rangeLabel.value} Steam 在线人数趋势` },
    color: [accent],
    tooltip: {
      trigger: 'axis',
      confine: true,
      valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')} 人`,
    },
    grid: { left: 8, right: 12, top: 12, bottom: 22, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: labels,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: muted,
        fontSize: 11,
        hideOverlap: true,
        formatter: (value: string) => {
          const [day, hm] = value.split(' ')
          return sameDay ? (hm ?? value) : (day ?? value)
        },
      },
    },
    yAxis: {
      type: 'value',
      splitNumber: 3,
      minInterval: 1,
      axisLabel: {
        color: muted,
        fontSize: 11,
        formatter: (value: number) => fmtCompact(value),
      },
      splitLine: { lineStyle: { color: splitLine } },
    },
    series: [
      {
        name: '在线人数',
        type: 'line',
        smooth: 0.3,
        showSymbol: false,
        symbolSize: 6,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.14 },
        data: app.series.map(point => point.player_count),
      },
    ],
  }
}
</script>

<template>
  <div class="min-w-0 space-y-6">
    <div>
      <SectionNav>
        <NuxtLink
          to="/account?tab=custom&create=1&source=steam"
          class="inline-flex h-9 items-center gap-1 whitespace-nowrap rounded-full bg-chip px-3 text-[13px] font-medium text-ink-2 transition-colors hover:text-ink"
        >
          <Plus :size="14" aria-hidden="true" />
          定制监控
        </NuxtLink>
        <button
          type="button"
          :disabled="refreshing"
          class="inline-flex h-9 items-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] text-muted transition-colors hover:bg-chip hover:text-ink disabled:cursor-wait"
          @click="refreshAll"
        >
          <RefreshCw :size="14" :class="{ 'animate-spin': refreshing }" aria-hidden="true" />
          {{ refreshing ? '刷新中…' : '刷新' }}
        </button>
      </SectionNav>

      <div class="mt-2 flex flex-wrap items-end justify-between gap-4">
        <div class="wall-heading">
          <h1 class="flex items-center gap-2 text-3xl font-bold tracking-tight text-ink">
            <Activity :size="24" class="shrink-0 text-sky-600 dark:text-sky-400" aria-hidden="true" />
            <span>{{ currentMeta ? `${currentMeta.label} · Steam` : 'Steam 追踪' }}</span>
            <span v-if="isAll && apps.length" class="num text-lg font-medium text-muted">{{ apps.length }}</span>
          </h1>
        </div>
        <div v-if="apps.length" class="flex gap-1 rounded-xl border border-line bg-card p-1" role="group" aria-label="统计时间范围">
          <button
            v-for="item in RANGES"
            :key="item.hours"
            type="button"
            class="min-h-10 cursor-pointer rounded-lg px-3 text-sm transition-colors"
            :class="hours === item.hours ? 'bg-brand-soft font-semibold text-brand-ink' : 'text-muted hover:bg-chip'"
            :aria-pressed="hours === item.hours"
            @click="setHours(item.hours)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>
    </div>

    <section v-if="pulseStatus === 'pending' && !pulseData" class="panel px-4 py-12 text-center text-sm text-muted">
      加载中…
    </section>
    <section v-else-if="pulseError" role="alert" class="panel px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">
      Steam 数据暂时加载失败。<button type="button" class="ml-1 underline" @click="refreshAll">重新加载</button>
    </section>

    <!-- 跨分区总览模式 -->
    <template v-else-if="isAll">
      <div v-if="apps.length" class="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <article
          v-for="app in apps"
          :key="`${app.community ?? 'c'}-${app.app_id}`"
          class="panel flex min-w-0 flex-col p-5"
        >
          <header class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <NuxtLink
                  v-if="app.community"
                  :to="{ path: '/steam', query: { community: app.community } }"
                  class="inline-flex min-w-0 items-center gap-1.5 text-lg font-bold text-ink hover:text-brand-ink"
                >
                  <span v-if="gameBadge(app.community)" class="size-2 shrink-0 rounded-full" :class="gameBadge(app.community)!.dot" aria-hidden="true" />
                  <span class="truncate">{{ gameBadge(app.community)?.label ?? app.name }}</span>
                </NuxtLink>
                <span v-else class="truncate text-lg font-bold text-ink">{{ app.name }}</span>
                <span v-if="app.price_formatted" class="chip bg-chip px-2 py-0 text-[11px] text-ink-2">{{ app.price_formatted }}</span>
                <span
                  v-if="app.discount_percent > 0"
                  class="num rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-bold text-emerald-700 dark:text-emerald-300"
                >-{{ app.discount_percent }}%</span>
              </div>
              <p class="mono mt-0.5 truncate text-xs text-faint">{{ app.name }} · AppID {{ app.app_id }}</p>
            </div>
            <a
              :href="app.store_url"
              target="_blank"
              rel="noopener noreferrer"
              class="link inline-flex shrink-0 items-center gap-0.5 text-xs"
            >
              商店页
              <ArrowUpRight :size="13" aria-hidden="true" />
            </a>
          </header>

          <div class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div class="min-w-0">
              <p class="text-xs text-muted">当前在线</p>
              <p class="num mt-0.5 truncate text-xl font-bold text-ink">{{ fmtCount(app.player_count) }}</p>
            </div>
            <div class="min-w-0">
              <p class="text-xs text-muted">{{ hours === 24 ? '24h 峰值' : `${rangeLabel}峰值` }}</p>
              <p class="num mt-0.5 truncate text-xl font-bold text-ink">{{ fmtCount(hours === 24 ? app.peak_24h : app.peak_window) }}</p>
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-1">
                <p class="text-xs text-muted">全区好评</p>
                <span
                  v-if="app.global_score_label"
                  class="chip truncate px-1.5 py-0 text-[10px] font-medium"
                  :class="badgeTone(app.global_rate)"
                >{{ app.global_score_label }}</span>
              </div>
              <p
                class="num mt-0.5 truncate text-xl font-bold"
                :class="rateTone(app.global_rate)"
                :title="reviewTotal(app.global_positive, app.global_negative) != null ? `全区共 ${reviewTotal(app.global_positive, app.global_negative)!.toLocaleString('zh-CN')} 评` : undefined"
              >
                {{ app.global_rate != null ? `${app.global_rate.toFixed(1)}%` : '—' }}
              </p>
            </div>
            <div class="min-w-0">
              <p class="text-xs text-muted">简中好评</p>
              <p
                class="num mt-0.5 truncate text-xl font-bold"
                :class="rateTone(app.cn_rate)"
                :title="reviewTotal(app.cn_positive, app.cn_negative) != null ? `简中共 ${reviewTotal(app.cn_positive, app.cn_negative)!.toLocaleString('zh-CN')} 评` : undefined"
              >
                {{ app.cn_rate != null ? `${app.cn_rate.toFixed(1)}%` : '—' }}
              </p>
            </div>
          </div>

          <div v-if="app.series.length >= 2" class="mt-4 border-t border-line-soft pt-3">
            <StatisticsChart
              :option="buildChartOption(app)"
              height="9.5rem"
              :aria-label="`${app.name} 近 ${rangeLabel} Steam 在线人数趋势`"
            />
          </div>

          <footer class="mt-auto flex items-center justify-between gap-2 border-t border-line-soft pt-3 text-xs text-faint">
            <time class="mono" :datetime="app.captured_at">更新 {{ fmtTime(app.captured_at) }}</time>
            <NuxtLink
              v-if="app.community"
              :to="{ path: '/steam', query: { community: app.community } }"
              class="font-medium text-brand-ink hover:underline"
            >
              查看详情 →
            </NuxtLink>
          </footer>
        </article>
      </div>
      <section v-else class="panel px-5 py-12 text-center">
        <p class="text-sm text-muted">当前暂无已接入的 Steam 游戏数据。</p>
        <NuxtLink to="/account?tab=custom&create=1&source=steam" class="btn-primary mt-4 inline-flex h-9 items-center gap-1.5 px-4 text-sm">
          <Plus :size="15" aria-hidden="true" />
          定制监控 Steam 游戏
        </NuxtLink>
      </section>
    </template>

    <!-- 单游戏分区模式 -->
    <template v-else-if="activeApp">
      <div v-if="apps.length > 1" class="flex flex-wrap gap-2" role="group" aria-label="切换 Steam 应用">
        <button
          v-for="app in apps"
          :key="app.app_id"
          type="button"
          class="rounded-full border px-3 py-1.5 text-sm transition-colors"
          :class="app.app_id === activeApp.app_id ? 'border-ink/70 bg-chip font-semibold text-ink' : 'border-line text-muted hover:text-ink'"
          :aria-pressed="app.app_id === activeApp.app_id"
          @click="selectedAppId = app.app_id"
        >
          {{ app.name }}
        </button>
      </div>

      <div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <section class="panel p-4">
          <p class="text-xs text-muted">当前在线</p>
          <p class="num mt-1 text-2xl font-bold text-ink sm:text-3xl">{{ fmtCount(activeApp.player_count) }}</p>
          <p class="mono mt-1 text-xs text-faint">{{ activeApp.name }}</p>
        </section>
        <section class="panel p-4">
          <p class="text-xs text-muted">24 小时峰值</p>
          <p class="num mt-1 text-2xl font-bold text-ink sm:text-3xl">{{ fmtCount(activeApp.peak_24h) }}</p>
          <p class="mt-1 text-xs text-faint">
            {{ rangeLabel }}峰值 <span class="num text-ink-2">{{ fmtCount(activeApp.peak_window) }}</span>
          </p>
        </section>
        <section class="panel p-4">
          <div class="flex items-center gap-1.5">
            <p class="text-xs text-muted">全区好评率</p>
            <span
              v-if="activeApp.global_score_label"
              class="chip px-1.5 py-0 text-[10px] font-medium"
              :class="badgeTone(activeApp.global_rate)"
            >{{ activeApp.global_score_label }}</span>
          </div>
          <p class="num mt-1 text-2xl font-bold sm:text-3xl" :class="rateTone(activeApp.global_rate)">
            {{ activeApp.global_rate != null ? `${activeApp.global_rate.toFixed(1)}%` : '—' }}
          </p>
          <p v-if="reviewTotal(activeApp.global_positive, activeApp.global_negative) != null" class="mt-1 text-xs text-faint">
            共 <span class="num text-ink-2">{{ reviewTotal(activeApp.global_positive, activeApp.global_negative)!.toLocaleString('zh-CN') }}</span> 条评测
          </p>
        </section>
        <section class="panel p-4">
          <div class="flex items-center gap-1.5">
            <p class="text-xs text-muted">简中好评率</p>
            <span
              v-if="activeApp.discount_percent > 0"
              class="num rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700 dark:text-emerald-300"
            >-{{ activeApp.discount_percent }}%</span>
          </div>
          <p class="num mt-1 text-2xl font-bold sm:text-3xl" :class="rateTone(activeApp.cn_rate)">
            {{ activeApp.cn_rate != null ? `${activeApp.cn_rate.toFixed(1)}%` : '—' }}
          </p>
          <p class="mt-1 truncate text-xs text-faint">
            <template v-if="reviewTotal(activeApp.cn_positive, activeApp.cn_negative) != null">
              简中 <span class="num text-ink-2">{{ fmtCompact(reviewTotal(activeApp.cn_positive, activeApp.cn_negative)!) }}</span> 评
            </template>
            <template v-if="activeApp.price_formatted">
              {{ reviewTotal(activeApp.cn_positive, activeApp.cn_negative) != null ? ' · ' : '' }}{{ activeApp.price_formatted }}
            </template>
          </p>
        </section>
      </div>

      <section class="panel p-5">
        <header class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="text-base font-bold text-ink">在线人数趋势 · 近 {{ rangeLabel }}</h2>
            <time class="mono text-xs text-faint" :datetime="activeApp.captured_at">更新于 {{ fmtTime(activeApp.captured_at) }}</time>
          </div>
          <a
            :href="activeApp.store_url"
            target="_blank"
            rel="noopener noreferrer"
            class="link inline-flex items-center gap-1 text-sm"
          >
            前往 Steam 商店
            <ArrowUpRight :size="14" aria-hidden="true" />
          </a>
        </header>
        <div v-if="activeApp.series.length >= 2" class="mt-4">
          <StatisticsChart
            :option="buildChartOption(activeApp)"
            height="16rem"
            :aria-label="`${activeApp.name} 近 ${rangeLabel} Steam 在线人数趋势`"
          />
        </div>
        <p v-else class="py-10 text-center text-sm text-muted">
          正在积累采样点，每 30 分钟自动更新一次，满 2 个点后自动绘制曲线。
        </p>
      </section>
    </template>

    <!-- 当前单游戏分区未接入 Steam -->
    <section v-else class="panel px-5 py-12 text-center">
      <p class="text-sm text-muted">当前分区暂未接入 Steam 数据。</p>
      <div class="mt-4 flex flex-wrap justify-center gap-3">
        <NuxtLink :to="{ path: '/steam', query: { community: 'all' } }" class="inline-flex h-9 items-center rounded-[10px] border border-line bg-card px-4 text-sm font-medium text-ink-2 hover:text-ink">
          查看全站 Steam 追踪
        </NuxtLink>
        <NuxtLink to="/account?tab=custom&create=1&source=steam" class="btn-primary inline-flex h-9 items-center gap-1.5 px-4 text-sm">
          <Plus :size="15" aria-hidden="true" />
          定制监控 Steam 游戏
        </NuxtLink>
      </div>
    </section>

    <!-- Steam 官方公告列表 -->
    <section v-if="isAll || activeApp || notices.length" class="panel overflow-hidden" aria-labelledby="steam-notices-title">
      <header class="flex items-center justify-between gap-3 border-b border-line px-4 py-3.5 sm:px-5">
        <h2 id="steam-notices-title" class="text-base font-bold text-ink">
          Steam 官方公告
          <span v-if="totalNotices" class="num ml-1.5 text-sm font-medium text-muted">{{ totalNotices }}</span>
        </h2>
        <NuxtLink
          :to="{ path: '/contents', query: { community: scope, platform: 'steam' } }"
          class="link text-[13px]"
        >
          在内容库查看
        </NuxtLink>
      </header>
      <p v-if="noticesStatus === 'pending' && !notices.length" class="px-4 py-12 text-center text-sm text-muted">加载中…</p>
      <p v-else-if="noticesError" role="alert" class="px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">公告暂时加载失败。</p>
      <div v-else>
        <RawFeedRow v-for="row in notices" :key="row.id" :item="row" :show-community="isAll" />
        <p v-if="!notices.length" class="px-4 py-12 text-center text-sm text-muted">暂无 Steam 官方公告。</p>
      </div>
      <footer v-if="totalPages > 1" class="flex items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-xs text-muted sm:px-5">
        <span>共 <span class="num font-semibold text-ink-2">{{ totalNotices }}</span> 条</span>
        <div class="flex items-center gap-1">
          <button type="button" :disabled="page <= 1" class="h-9 rounded-[10px] px-3 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40" @click="goPage(page - 1)">上一页</button>
          <span class="mono px-1.5">{{ page }} / {{ totalPages }}</span>
          <button type="button" :disabled="page >= totalPages" class="h-9 rounded-[10px] px-3 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40" @click="goPage(page + 1)">下一页</button>
        </div>
      </footer>
    </section>
  </div>
</template>
