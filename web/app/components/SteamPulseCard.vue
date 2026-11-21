<script setup lang="ts">
import { Activity, ArrowUpRight } from 'lucide-vue-next'
import type { EChartsCoreOption } from 'echarts/core'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import type { SteamPulseApp } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

// Steam 游戏级实时遥测卡：
// - overview：聚合首页顶部横栏，展示各游戏实时在线与好评速览，点击跳入对应分区；
// - banner：单游戏分区首屏横栏，展示核心指标 + 内联近 7 日在线曲线；
// - sidebar：定制监控详情页侧栏竖版卡片。
const props = withDefaults(defineProps<{
  apps: SteamPulseApp[]
  layout?: 'banner' | 'overview' | 'sidebar'
}>(), {
  layout: 'sidebar',
})

const theme = useState<'light' | 'dark'>('theme', () => 'light')
const selectedId = ref<number | null>(props.apps[0]?.app_id ?? null)

watch(
  () => props.apps,
  (list) => {
    if (!list.length) {
      selectedId.value = null
      return
    }
    if (!list.some(item => item.app_id === selectedId.value)) {
      selectedId.value = list[0]!.app_id
    }
  },
)

const activeApp = computed<SteamPulseApp | null>(
  () => props.apps.find(item => item.app_id === selectedId.value) ?? props.apps[0] ?? null,
)

const latestCapturedAt = computed(() => {
  if (!props.apps.length) return ''
  return props.apps.reduce((best, app) => (app.captured_at > best ? app.captured_at : best), props.apps[0]!.captured_at)
})

const overviewRows = computed(() =>
  props.apps.map((app) => {
    const meta = communityMeta(app.community)
    return {
      app,
      label: meta?.label ?? app.name,
      dot: meta ? communityClasses(meta.color).dot : 'bg-sky-500',
      to: app.community ? { path: '/', query: { community: app.community } } : null,
    }
  }),
)

const globalTotal = computed(() => {
  const app = activeApp.value
  if (!app || app.global_positive == null || app.global_negative == null) return null
  return app.global_positive + app.global_negative
})

const cnTotal = computed(() => {
  const app = activeApp.value
  if (!app || app.cn_positive == null || app.cn_negative == null) return null
  return app.cn_positive + app.cn_negative
})

function fmtCount(value: number | null): string {
  return value == null ? '—' : value.toLocaleString('zh-CN')
}

function fmtCompact(value: number): string {
  return new Intl.NumberFormat('zh-CN', {
    notation: value >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(value)
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

function rateToneClass(rate: number | null): string {
  if (rate == null) return 'text-ink'
  if (rate >= 70) return 'text-emerald-600 dark:text-emerald-400'
  if (rate >= 40) return 'text-amber-600 dark:text-amber-400'
  return 'text-hot'
}

function badgeToneClass(rate: number | null): string {
  if (rate == null) return 'bg-chip text-muted'
  if (rate >= 70) return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  if (rate >= 40) return 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  return 'bg-red-500/10 text-hot'
}

const chartOption = computed<EChartsCoreOption>(() => {
  const app = activeApp.value
  const series = app?.series ?? []
  const labels = series.map(point => fmtPointTime(point.ts))
  const uniqueDays = new Set(labels.map(label => label.split(' ')[0]))
  const sameDay = uniqueDays.size <= 1
  const muted = theme.value === 'dark' ? '#a1a1aa' : '#64748b'
  const splitLine = theme.value === 'dark' ? '#27272a' : '#e2e8f0'
  const accent = theme.value === 'dark' ? '#38bdf8' : '#0284c7'

  return {
    aria: { enabled: true, description: `${app?.name ?? 'Steam'} 近 7 日在线人数趋势` },
    color: [accent],
    tooltip: {
      trigger: 'axis',
      confine: true,
      valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')} 人`,
    },
    grid: { left: 4, right: 6, top: 6, bottom: 18, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: labels,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: muted,
        fontSize: 10,
        hideOverlap: true,
        formatter: (value: string) => {
          const [day, hm] = value.split(' ')
          return sameDay ? (hm ?? value) : (day ?? value)
        },
      },
    },
    yAxis: {
      type: 'value',
      splitNumber: 2,
      minInterval: 1,
      axisLabel: {
        color: muted,
        fontSize: 10,
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
        symbolSize: 5,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.14 },
        data: series.map(point => point.player_count),
      },
    ],
  }
})

const chartAriaLabel = computed(() => {
  const app = activeApp.value
  if (!app) return 'Steam 在线人数趋势图'
  return `${app.name} Steam 近 7 日在线人数趋势，当前在线 ${fmtCount(app.player_count)}`
})
</script>

<template>
  <!-- 聚合首页：多游戏 Steam 实时在线与好评总览条 -->
  <section v-if="layout === 'overview' && overviewRows.length" class="panel mb-5 px-4 py-3.5 sm:px-5" aria-labelledby="steam-pulse-title">
    <header class="flex items-center justify-between gap-2">
      <h2 id="steam-pulse-title" class="flex items-center gap-1.5 text-sm font-bold text-ink">
        <Activity :size="15" class="shrink-0 text-sky-600 dark:text-sky-400" aria-hidden="true" />
        <span>Steam 实时数据</span>
      </h2>
      <div class="flex shrink-0 items-center gap-3">
        <time v-if="latestCapturedAt" class="mono hidden text-[11px] text-faint sm:inline" :datetime="latestCapturedAt">
          更新 {{ fmtTime(latestCapturedAt) }}
        </time>
        <NuxtLink to="/account?tab=custom&create=1&source=steam" class="link text-xs">定制监控</NuxtLink>
      </div>
    </header>
    <div class="mt-2.5 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
      <NuxtLink
        v-for="row in overviewRows"
        :key="`${row.app.community ?? 'c'}-${row.app.app_id}`"
        :to="row.to ?? undefined"
        class="group block min-w-0 rounded-xl border border-line-soft bg-chip/40 px-3.5 py-2.5 transition-colors hover:border-faint/60 hover:bg-chip/80"
      >
        <div class="flex items-center justify-between gap-1.5">
          <span class="flex min-w-0 items-center gap-1.5">
            <span class="size-[7px] shrink-0 rounded-full" :class="row.dot" aria-hidden="true" />
            <span class="truncate text-[13px] font-semibold text-ink group-hover:text-brand-ink">{{ row.label }}</span>
          </span>
          <span
            v-if="row.app.discount_percent > 0"
            class="num shrink-0 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700 dark:text-emerald-300"
          >-{{ row.app.discount_percent }}%</span>
          <span
            v-else-if="row.app.global_score_label"
            class="chip shrink-0 px-1.5 py-0 text-[10px] font-medium"
            :class="badgeToneClass(row.app.global_rate)"
          >{{ row.app.global_score_label }}</span>
        </div>
        <p class="mt-1.5 flex items-baseline gap-1.5">
          <span class="num text-lg font-bold text-ink">{{ fmtCount(row.app.player_count) }}</span>
          <span class="text-[11px] text-muted">在线</span>
        </p>
        <p class="mt-0.5 truncate text-[11px] text-faint">
          <template v-if="row.app.global_rate != null">
            全区 <span class="num font-medium" :class="rateToneClass(row.app.global_rate)">{{ row.app.global_rate.toFixed(1) }}%</span>
          </template>
          <template v-if="row.app.cn_rate != null">
            · 简中 <span class="num font-medium" :class="rateToneClass(row.app.cn_rate)">{{ row.app.cn_rate.toFixed(1) }}%</span>
          </template>
          <template v-if="row.app.global_rate == null && row.app.cn_rate == null">
            {{ row.app.price_formatted || '暂无评测' }}
          </template>
        </p>
      </NuxtLink>
    </div>
  </section>

  <!-- 单游戏分区首页：首屏紧凑横幅（核心指标 + 内联 7 日曲线） -->
  <section v-else-if="layout === 'banner' && activeApp" class="panel mb-5 px-4 py-3.5 sm:px-5" aria-labelledby="steam-pulse-title">
    <header class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex min-w-0 flex-wrap items-center gap-2">
        <h2 id="steam-pulse-title" class="flex items-center gap-1.5 text-sm font-bold text-ink">
          <Activity :size="15" class="shrink-0 text-sky-600 dark:text-sky-400" aria-hidden="true" />
          <span>Steam 实时数据</span>
        </h2>
        <span class="truncate text-xs text-muted">{{ activeApp.name }}</span>
        <span v-if="activeApp.price_formatted" class="chip bg-chip px-2 py-0 text-[11px] text-ink-2">{{ activeApp.price_formatted }}</span>
        <span
          v-if="activeApp.discount_percent > 0"
          class="num rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-bold text-emerald-700 dark:text-emerald-300"
        >-{{ activeApp.discount_percent }}%</span>
        <div v-if="apps.length > 1" class="flex flex-wrap gap-1" role="group" aria-label="切换 Steam 应用">
          <button
            v-for="app in apps"
            :key="app.app_id"
            type="button"
            class="rounded-full border px-2 py-0.5 text-[11px] transition-colors"
            :class="app.app_id === activeApp.app_id ? 'border-ink/70 bg-chip font-semibold text-ink' : 'border-line text-muted hover:text-ink'"
            :aria-pressed="app.app_id === activeApp.app_id"
            @click="selectedId = app.app_id"
          >
            {{ app.name }}
          </button>
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-3">
        <time class="mono hidden text-[11px] text-faint sm:inline" :datetime="activeApp.captured_at">更新 {{ fmtTime(activeApp.captured_at) }}</time>
        <a
          :href="activeApp.store_url"
          target="_blank"
          rel="noopener noreferrer"
          class="link inline-flex items-center gap-0.5 text-xs"
        >
          商店页
          <ArrowUpRight :size="12" aria-hidden="true" />
        </a>
      </div>
    </header>

    <div
      class="mt-3 grid items-center gap-4"
      :class="activeApp.series.length >= 2 ? 'lg:grid-cols-[minmax(0,1fr)_280px]' : ''"
    >
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div class="min-w-0">
          <p class="text-xs text-muted">当前在线</p>
          <p class="num mt-0.5 truncate text-xl font-bold text-ink sm:text-2xl">{{ fmtCount(activeApp.player_count) }}</p>
        </div>
        <div class="min-w-0">
          <p class="text-xs text-muted">24h 峰值</p>
          <p class="num mt-0.5 truncate text-xl font-bold text-ink sm:text-2xl">{{ fmtCount(activeApp.peak_24h) }}</p>
        </div>
        <div class="min-w-0">
          <div class="flex items-center gap-1.5">
            <p class="text-xs text-muted">全区好评</p>
            <span
              v-if="activeApp.global_score_label"
              class="chip truncate px-1.5 py-0 text-[10px] font-medium"
              :class="badgeToneClass(activeApp.global_rate)"
            >{{ activeApp.global_score_label }}</span>
          </div>
          <p
            class="num mt-0.5 truncate text-xl font-bold sm:text-2xl"
            :class="rateToneClass(activeApp.global_rate)"
            :title="globalTotal != null ? `全区评价共 ${globalTotal.toLocaleString('zh-CN')} 条` : undefined"
          >
            {{ activeApp.global_rate != null ? `${activeApp.global_rate.toFixed(1)}%` : '—' }}
          </p>
        </div>
        <div class="min-w-0">
          <p class="text-xs text-muted">{{ activeApp.cn_rate != null ? '简中好评' : '评测总数' }}</p>
          <p
            v-if="activeApp.cn_rate != null"
            class="num mt-0.5 truncate text-xl font-bold sm:text-2xl"
            :class="rateToneClass(activeApp.cn_rate)"
            :title="cnTotal != null ? `简中评价共 ${cnTotal.toLocaleString('zh-CN')} 条` : undefined"
          >
            {{ activeApp.cn_rate.toFixed(1) }}%
            <span v-if="cnTotal != null" class="text-xs font-normal text-faint">· {{ fmtCompact(cnTotal) }}</span>
          </p>
          <p v-else class="num mt-0.5 truncate text-xl font-bold text-ink sm:text-2xl">
            {{ globalTotal != null ? fmtCompact(globalTotal) : '—' }}
          </p>
        </div>
      </div>

      <div v-if="activeApp.series.length >= 2" class="min-w-0 border-t border-line-soft pt-2 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
        <StatisticsChart :option="chartOption" height="5.25rem" :aria-label="chartAriaLabel" />
      </div>
    </div>
  </section>

  <!-- 侧栏竖版（用于定制监控详情页） -->
  <section v-else-if="activeApp" class="panel p-5" aria-labelledby="steam-pulse-title">
    <header class="flex items-center justify-between gap-2">
      <h2 id="steam-pulse-title" class="flex min-w-0 items-center gap-1.5 text-base font-bold text-ink">
        <Activity :size="16" class="shrink-0 text-sky-600 dark:text-sky-400" aria-hidden="true" />
        <span class="truncate">Steam 实时数据</span>
        <span
          v-if="activeApp.discount_percent > 0"
          class="num shrink-0 rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-bold text-emerald-700 dark:text-emerald-300"
        >-{{ activeApp.discount_percent }}%</span>
      </h2>
      <a
        :href="activeApp.store_url"
        target="_blank"
        rel="noopener noreferrer"
        class="link inline-flex shrink-0 items-center gap-0.5 text-[13px]"
      >
        商店页
        <ArrowUpRight :size="13" aria-hidden="true" />
      </a>
    </header>

    <div v-if="apps.length > 1" class="mt-2.5 flex flex-wrap gap-1.5" role="group" aria-label="切换 Steam 应用">
      <button
        v-for="app in apps"
        :key="app.app_id"
        type="button"
        class="rounded-full border px-2.5 py-1 text-xs transition-colors"
        :class="app.app_id === activeApp.app_id ? 'border-ink/70 bg-chip font-semibold text-ink' : 'border-line text-muted hover:text-ink'"
        :aria-pressed="app.app_id === activeApp.app_id"
        @click="selectedId = app.app_id"
      >
        {{ app.name }}
      </button>
    </div>

    <div class="mt-3 grid grid-cols-2 gap-3">
      <div class="min-w-0">
        <p class="text-xs text-muted">当前在线</p>
        <p class="num mt-0.5 truncate text-xl font-bold text-ink">{{ fmtCount(activeApp.player_count) }}</p>
        <p class="mt-0.5 truncate text-xs text-faint">
          24h 峰值 <span class="num text-ink-2">{{ fmtCount(activeApp.peak_24h) }}</span>
        </p>
      </div>
      <div class="min-w-0">
        <div class="flex items-center gap-1.5">
          <p class="text-xs text-muted">全区好评</p>
          <span
            v-if="activeApp.global_score_label"
            class="chip truncate px-1.5 py-0 text-[10px] font-medium"
            :class="badgeToneClass(activeApp.global_rate)"
          >{{ activeApp.global_score_label }}</span>
        </div>
        <p
          class="num mt-0.5 truncate text-xl font-bold"
          :class="rateToneClass(activeApp.global_rate)"
          :title="globalTotal != null ? `全区评价共 ${globalTotal.toLocaleString('zh-CN')} 条` : undefined"
        >
          {{ activeApp.global_rate != null ? `${activeApp.global_rate.toFixed(1)}%` : '—' }}
        </p>
        <p
          v-if="activeApp.cn_rate != null"
          class="mt-0.5 truncate text-xs text-faint"
          :title="cnTotal != null ? `简中评价共 ${cnTotal.toLocaleString('zh-CN')} 条` : undefined"
        >
          简中 <span class="num font-medium" :class="rateToneClass(activeApp.cn_rate)">{{ activeApp.cn_rate.toFixed(1) }}%</span>
          <span v-if="cnTotal != null"> · {{ fmtCompact(cnTotal) }}评</span>
        </p>
        <p v-else-if="globalTotal != null" class="mt-0.5 truncate text-xs text-faint">
          共 <span class="num text-ink-2">{{ fmtCompact(globalTotal) }}</span> 评
        </p>
      </div>
    </div>

    <div
      v-if="activeApp.series.length >= 2"
      class="mt-3 border-t border-line-soft pt-2.5"
    >
      <StatisticsChart :option="chartOption" height="7.5rem" :aria-label="chartAriaLabel" />
    </div>

    <footer class="mt-2.5 flex items-center justify-between gap-2 border-t border-line-soft pt-2 text-[11px] text-faint">
      <span class="truncate">
        <template v-if="activeApp.price_formatted">{{ activeApp.price_formatted }}</template>
        <template v-else-if="globalTotal != null">全区 {{ fmtCompact(globalTotal) }} 评</template>
        <template v-else>{{ activeApp.name }}</template>
      </span>
      <time class="mono shrink-0" :datetime="activeApp.captured_at">更新 {{ fmtTime(activeApp.captured_at) }}</time>
    </footer>
  </section>
</template>
