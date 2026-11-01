<script setup lang="ts">
import { Activity, Clock3, Database, Gauge, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import type { EChartsCoreOption } from 'echarts/core'
import type { StatisticsOverview } from '~/composables/usePublicApi'
import { platformMeta } from '~/utils/data'
import { fmtTime } from '~/utils/admin-types'

const ranges = [
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
  { value: 365, label: '1 年' },
]
const range = ref(30)
const refreshing = ref(false)
const theme = useState<'light' | 'dark'>('theme', () => 'light')
const { api } = usePublicApi()
const request = useLazyAsyncData(
  () => `public-statistics-${range.value}`,
  (_app, { signal }) => api<StatisticsOverview>('/api/statistics', { query: { days: range.value }, signal }),
  { watch: [range] },
)
const { data, status, error } = await request

const muted = computed(() => theme.value === 'dark' ? '#a1a1aa' : '#64748b')
const grid = { left: 12, right: 16, top: 38, bottom: 12, containLabel: true }

function platformLabel(key: string) {
  return platformMeta[key as keyof typeof platformMeta]?.label || key
}

function compact(value: number) {
  return new Intl.NumberFormat('zh-CN', { notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value)
}

function percent(value: number) {
  return `${value.toFixed(1)}%`
}

function duration(value: number | null) {
  if (value === null) return '—'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(1)} s`
}

function statusLabel(status: string) {
  return { running: '进行中', success: '成功', partial: '部分成功', failed: '失败', skipped: '冷却跳过' }[status] || status
}

function statusClass(status: string) {
  if (status === 'running') return 'bg-blue-500/10 text-blue-700 dark:text-blue-300'
  if (status === 'failed') return 'bg-red-500/10 text-hot'
  if (status === 'partial') return 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  if (status === 'skipped') return 'bg-chip text-muted'
  return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
}

function shortDate(value: string) {
  const [, month, day] = value.split('-')
  return `${month}/${day}`
}

const trendOption = computed<EChartsCoreOption>(() => ({
  aria: { enabled: true, description: `最近 ${range.value} 天官方与社区新入库趋势` },
  color: ['#2563eb', '#14b8a6'],
  tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${Number(value).toLocaleString()} 条` },
  legend: { top: 0, textStyle: { color: muted.value }, data: ['官方', '社区线索'] },
  grid,
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: data.value?.trend.map(item => shortDate(item.date)) || [],
    axisLabel: { color: muted.value, hideOverlap: true },
    axisLine: { lineStyle: { color: theme.value === 'dark' ? '#3f3f46' : '#cbd5e1' } },
  },
  yAxis: {
    type: 'value',
    minInterval: 1,
    axisLabel: { color: muted.value },
    splitLine: { lineStyle: { color: theme.value === 'dark' ? '#27272a' : '#e2e8f0' } },
  },
  series: [
    {
      name: '官方', type: 'line', stack: 'contents', smooth: true, showSymbol: false,
      areaStyle: { opacity: 0.16 }, lineStyle: { width: 2 },
      data: data.value?.trend.map(item => item.official) || [],
    },
    {
      name: '社区线索', type: 'line', stack: 'contents', smooth: true, showSymbol: false,
      areaStyle: { opacity: 0.12 }, lineStyle: { width: 2 },
      data: data.value?.trend.map(item => item.community) || [],
    },
  ],
}))

const platformOption = computed<EChartsCoreOption>(() => {
  const rows = [...(data.value?.platforms || [])].reverse()
  return {
    aria: { enabled: true, description: `最近 ${range.value} 天各平台官方与社区入库对比` },
    color: ['#2563eb', '#14b8a6'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, textStyle: { color: muted.value }, data: ['官方', '社区线索'] },
    grid,
    xAxis: {
      type: 'value', minInterval: 1,
      axisLabel: { color: muted.value },
      splitLine: { lineStyle: { color: theme.value === 'dark' ? '#27272a' : '#e2e8f0' } },
    },
    yAxis: {
      type: 'category', data: rows.map(item => platformLabel(item.platform)),
      axisLabel: { color: muted.value }, axisLine: { show: false }, axisTick: { show: false },
    },
    series: [
      { name: '官方', type: 'bar', stack: 'contents', barMaxWidth: 22, data: rows.map(item => item.official) },
      { name: '社区线索', type: 'bar', stack: 'contents', barMaxWidth: 22, data: rows.map(item => item.community) },
    ],
  }
})

const shareOption = computed<EChartsCoreOption>(() => ({
  aria: { enabled: true, description: `最近 ${range.value} 天各平台入库占比` },
  color: ['#2563eb', '#0ea5e9', '#14b8a6', '#f59e0b', '#64748b'],
  tooltip: { trigger: 'item', formatter: '{b}<br/>{c} 条 · {d}%' },
  legend: { type: 'scroll', bottom: 0, textStyle: { color: muted.value } },
  series: [{
    type: 'pie', radius: ['48%', '72%'], center: ['50%', '43%'],
    itemStyle: { borderRadius: 5, borderWidth: 2, borderColor: theme.value === 'dark' ? '#18181b' : '#f8fafc' },
    label: { color: muted.value, formatter: '{b}\n{d}%' },
    data: data.value?.platforms.map(item => ({ name: platformLabel(item.platform), value: item.total })) || [],
  }],
}))

const targetOption = computed<EChartsCoreOption>(() => {
  const rows = [...(data.value?.targets || [])].slice(0, 8).reverse()
  return {
    aria: { enabled: true, description: `最近 ${range.value} 天采集目标命中与新入库数量` },
    color: ['#94a3b8', '#2563eb'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, textStyle: { color: muted.value }, data: ['采集命中', '新入库'] },
    grid: { left: 12, right: 16, top: 38, bottom: 12, containLabel: true },
    xAxis: {
      type: 'value', minInterval: 1,
      axisLabel: { color: muted.value },
      splitLine: { lineStyle: { color: theme.value === 'dark' ? '#27272a' : '#e2e8f0' } },
    },
    yAxis: {
      type: 'category', data: rows.map(item => item.label),
      axisLabel: { color: muted.value, width: 110, overflow: 'truncate' },
      axisLine: { show: false }, axisTick: { show: false },
    },
    series: [
      { name: '采集命中', type: 'bar', barMaxWidth: 12, data: rows.map(item => item.items_found) },
      { name: '新入库', type: 'bar', barMaxWidth: 12, data: rows.map(item => item.items_new) },
    ],
  }
})

const collectionOption = computed<EChartsCoreOption>(() => ({
  aria: { enabled: true, description: `最近 ${range.value} 天采集命中与新入库趋势` },
  color: ['#64748b', '#2563eb'],
  tooltip: { trigger: 'axis' },
  legend: { top: 0, textStyle: { color: muted.value }, data: ['采集命中', '新入库'] },
  grid,
  xAxis: {
    type: 'category', boundaryGap: false,
    data: data.value?.collection.map(item => shortDate(item.date)) || [],
    axisLabel: { color: muted.value, hideOverlap: true },
    axisLine: { lineStyle: { color: theme.value === 'dark' ? '#3f3f46' : '#cbd5e1' } },
  },
  yAxis: {
    type: 'value', minInterval: 1, axisLabel: { color: muted.value },
    splitLine: { lineStyle: { color: theme.value === 'dark' ? '#27272a' : '#e2e8f0' } },
  },
  series: [
    { name: '采集命中', type: 'line', smooth: true, showSymbol: false, data: data.value?.collection.map(item => item.items_found) || [] },
    { name: '新入库', type: 'line', smooth: true, showSymbol: false, lineStyle: { width: 2.5 }, data: data.value?.collection.map(item => item.items_new) || [] },
  ],
}))

const keywordOption = computed<EChartsCoreOption>(() => {
  const rows = [...(data.value?.keywords || [])].reverse()
  return {
    aria: { enabled: true, description: `最近 ${range.value} 天内容关键词入库数量` },
    color: ['#0ea5e9'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 12, right: 20, top: 10, bottom: 12, containLabel: true },
    xAxis: {
      type: 'value', minInterval: 1, axisLabel: { color: muted.value },
      splitLine: { lineStyle: { color: theme.value === 'dark' ? '#27272a' : '#e2e8f0' } },
    },
    yAxis: {
      type: 'category', data: rows.map(item => item.keyword),
      axisLabel: { color: muted.value, width: 130, overflow: 'truncate' }, axisLine: { show: false }, axisTick: { show: false },
    },
    series: [{ type: 'bar', barMaxWidth: 18, label: { show: true, position: 'right', color: muted.value }, data: rows.map(item => item.contents) }],
  }
})

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await request.refresh()
  }
  finally {
    refreshing.value = false
  }
}

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => {
    if (document.visibilityState === 'visible') void refresh()
  }, 60_000)
})
onBeforeUnmount(() => timer && clearInterval(timer))

useSeoMeta({ title: '数据统计' })
</script>

<template>
  <div class="space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div class="wall-heading">
        <p class="mono mb-1.5 text-xs tracking-wide text-muted">
          {{ data?.totals.latest_collection_at ? `最近采集 ${fmtTime(data.totals.latest_collection_at)}` : '每日入库与采集趋势' }}
        </p>
        <h1 class="text-[28px] font-bold leading-tight text-ink sm:text-[32px]">数据统计</h1>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <div class="wall-surface flex gap-1 rounded-xl bg-chip p-1" role="group" aria-label="统计时间范围">
          <button
            v-for="item in ranges"
            :key="item.value"
            type="button"
            class="h-8 rounded-[9px] px-3 text-[13px] transition-colors"
            :class="range === item.value ? 'bg-card font-semibold text-ink shadow-card' : 'text-muted hover:text-ink'"
            :aria-pressed="range === item.value"
            @click="range = item.value"
          >
            {{ item.label }}
          </button>
        </div>
        <button
          type="button"
          :disabled="refreshing"
          class="grid size-10 place-items-center rounded-[10px] text-muted transition-colors hover:bg-chip hover:text-ink"
          aria-label="刷新统计"
          @click="refresh"
        >
          <RefreshCw :size="17" :class="{ 'animate-spin': refreshing }" aria-hidden="true" />
        </button>
      </div>
    </header>

    <section v-if="status === 'pending' && !data" class="panel px-5 py-20 text-center text-sm text-muted">
      正在汇总统计…
    </section>
    <section v-else-if="error" class="panel px-5 py-16 text-center text-sm text-hot">
      统计加载失败。<button type="button" class="ml-1 underline" @click="refresh">重新加载</button>
    </section>

    <template v-else-if="data">
      <section class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="统计概览">
        <article class="panel p-4">
          <div class="flex items-center justify-between text-muted">
            <span class="text-xs">累计入库</span><Database :size="16" aria-hidden="true" />
          </div>
          <p class="num mt-2 text-2xl font-bold text-ink">{{ data.totals.total_contents.toLocaleString() }}</p>
          <p class="mt-1 text-xs text-muted">本期新增 {{ data.totals.period_new.toLocaleString() }}</p>
        </article>
        <article class="panel p-4">
          <div class="flex items-center justify-between text-muted">
            <span class="text-xs">官方 / 社区</span><ShieldCheck :size="16" aria-hidden="true" />
          </div>
          <p class="num mt-2 text-2xl font-bold text-ink">
            {{ data.totals.official_contents.toLocaleString() }} <span class="text-sm font-normal text-faint">/</span> {{ data.totals.community_contents.toLocaleString() }}
          </p>
          <p class="mt-1 text-xs text-muted">累计内容构成</p>
        </article>
        <article class="panel p-4">
          <div class="flex items-center justify-between text-muted">
            <span class="text-xs">完整成功率</span><Activity :size="16" aria-hidden="true" />
          </div>
          <p class="num mt-2 text-2xl font-bold text-ink">{{ percent(data.totals.success_rate) }}</p>
          <p class="mt-1 text-xs text-muted">{{ data.totals.collection_runs }} 次 · 失败 {{ data.totals.failed_runs }} 次</p>
          <p class="mt-1 text-xs text-muted">仅完整成功计入；部分成功计入总次数，冷却跳过不计入。</p>
        </article>
        <article class="panel p-4">
          <div class="flex items-center justify-between text-muted">
            <span class="text-xs">入库转化率</span><Gauge :size="16" aria-hidden="true" />
          </div>
          <p class="num mt-2 text-2xl font-bold text-ink">{{ percent(data.totals.yield_rate) }}</p>
          <p class="mt-1 text-xs text-muted">命中 {{ compact(data.totals.items_found) }} · 新增 {{ compact(data.totals.items_new) }}</p>
        </article>
      </section>

      <section class="grid gap-4 xl:grid-cols-2">
        <article class="panel min-w-0 p-4">
          <h2 class="text-base font-bold text-ink">新入库趋势</h2>
          <ClientOnly>
            <StatisticsChart :option="trendOption" aria-label="官方与社区新入库趋势图" />
            <template #fallback><div class="h-72" aria-hidden="true" /></template>
          </ClientOnly>
        </article>
        <article class="panel min-w-0 p-4">
          <h2 class="text-base font-bold text-ink">平台对比</h2>
          <ClientOnly>
            <StatisticsChart :option="platformOption" aria-label="各平台官方与社区入库对比图" />
            <template #fallback><div class="h-72" aria-hidden="true" /></template>
          </ClientOnly>
        </article>
        <article class="panel min-w-0 p-4">
          <h2 class="text-base font-bold text-ink">平台占比</h2>
          <ClientOnly>
            <StatisticsChart :option="shareOption" aria-label="各平台入库占比图" />
            <template #fallback><div class="h-72" aria-hidden="true" /></template>
          </ClientOnly>
        </article>
        <article class="panel min-w-0 p-4">
          <h2 class="text-base font-bold text-ink">采集效率</h2>
          <ClientOnly>
            <StatisticsChart :option="collectionOption" aria-label="采集命中与新入库趋势图" />
            <template #fallback><div class="h-72" aria-hidden="true" /></template>
          </ClientOnly>
        </article>
        <article class="panel min-w-0 p-4">
          <h2 class="text-base font-bold text-ink">采集目标</h2>
          <ClientOnly>
            <StatisticsChart :option="targetOption" aria-label="采集目标命中与新增对比图" />
            <template #fallback><div class="h-72" aria-hidden="true" /></template>
          </ClientOnly>
        </article>
        <article class="panel min-w-0 p-4">
          <h2 class="text-base font-bold text-ink">内容关键词</h2>
          <ClientOnly v-if="data.keywords.length">
            <StatisticsChart :option="keywordOption" aria-label="内容关键词入库排行图" />
            <template #fallback><div class="h-72" aria-hidden="true" /></template>
          </ClientOnly>
          <div v-else class="grid h-72 place-items-center text-sm text-faint">本期暂无关键词记录</div>
        </article>
      </section>

      <section class="panel overflow-hidden">
        <header class="flex items-center justify-between gap-3 border-b border-line-soft px-4 py-3">
          <h2 class="text-base font-bold text-ink">最近采集</h2>
          <span v-if="data.details_since" class="mono text-[11px] text-faint">明细始于 {{ fmtTime(data.details_since) }}</span>
        </header>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[760px] text-left text-xs">
            <thead class="bg-chip/60 text-muted">
              <tr>
                <th class="px-4 py-2.5 font-medium">时间</th>
                <th class="px-3 py-2.5 font-medium">目标</th>
                <th class="px-3 py-2.5 font-medium">平台</th>
                <th class="px-3 py-2.5 font-medium">状态</th>
                <th class="px-3 py-2.5 text-right font-medium">抓取</th>
                <th class="px-3 py-2.5 text-right font-medium">过滤</th>
                <th class="px-3 py-2.5 text-right font-medium">命中</th>
                <th class="px-3 py-2.5 text-right font-medium">新增</th>
                <th class="px-4 py-2.5 text-right font-medium">耗时</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line-soft">
              <tr v-for="run in data.recent_runs" :key="run.id" class="transition-colors hover:bg-chip/60">
                <td class="mono whitespace-nowrap px-4 py-2.5 text-muted">{{ fmtTime(run.finished_at || run.started_at) }}</td>
                <td class="max-w-52 truncate px-3 py-2.5 font-medium text-ink">{{ run.target }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 text-ink-2">{{ platformLabel(run.platform) }}</td>
                <td class="px-3 py-2.5">
                  <span class="rounded px-1.5 py-0.5" :class="statusClass(run.status)">
                    {{ statusLabel(run.status) }}
                  </span>
                </td>
                <td class="mono px-3 py-2.5 text-right">{{ run.items_fetched ?? '—' }}</td>
                <td class="mono px-3 py-2.5 text-right">{{ run.items_filtered ?? '—' }}</td>
                <td class="mono px-3 py-2.5 text-right">{{ run.items_found }}</td>
                <td class="mono px-3 py-2.5 text-right text-blue-700 dark:text-blue-300">{{ run.items_new }}</td>
                <td class="mono whitespace-nowrap px-4 py-2.5 text-right text-muted">{{ duration(run.duration_ms) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <details class="panel group overflow-hidden">
        <summary class="flex min-h-11 cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-medium text-ink-2">
          精确数据
          <Clock3 :size="15" class="text-faint transition-transform group-open:rotate-90" aria-hidden="true" />
        </summary>
        <div class="overflow-x-auto border-t border-line-soft">
          <table class="w-full min-w-[560px] text-left text-xs">
            <thead class="bg-chip/60 text-muted">
              <tr><th class="px-4 py-2 font-medium">平台</th><th class="px-3 py-2 text-right font-medium">官方</th><th class="px-3 py-2 text-right font-medium">社区</th><th class="px-3 py-2 text-right font-medium">合计</th><th class="px-4 py-2 text-right font-medium">占比</th></tr>
            </thead>
            <tbody class="divide-y divide-line-soft">
              <tr v-for="item in data.platforms" :key="item.platform">
                <td class="px-4 py-2.5 font-medium">{{ platformLabel(item.platform) }}</td>
                <td class="mono px-3 py-2.5 text-right">{{ item.official.toLocaleString() }}</td>
                <td class="mono px-3 py-2.5 text-right">{{ item.community.toLocaleString() }}</td>
                <td class="mono px-3 py-2.5 text-right">{{ item.total.toLocaleString() }}</td>
                <td class="mono px-4 py-2.5 text-right">{{ percent(item.share) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </template>
  </div>
</template>
