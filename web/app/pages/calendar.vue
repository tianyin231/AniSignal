<script setup lang="ts">
import { ArrowUpRight, ChevronDown, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-vue-next'
import { GAME_COMMUNITIES, communityClasses } from '~/composables/useGameSection'
import type { CalendarEntry } from '~/composables/usePublicApi'
import { beijingDay, calendarDays, shiftCalendarMonth, type CalendarMilestone } from '~/utils/calendar'
import { fmtTime } from '~/utils/admin-types'
import { decodeEntities } from '~/utils/text'

useSeoMeta({ title: '活动日历 · AniSignal' })

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()
const today = useState('calendar-today', () => beijingDay(Date.now()))
const month = computed(() => /^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$/.test(String(route.query.month ?? ''))
  ? String(route.query.month) : today.value.slice(0, 7))
const monthLabel = computed(() => `${Number(month.value.slice(0, 4))} 年 ${Number(month.value.slice(5))} 月`)
const selectedDay = ref(month.value === today.value.slice(0, 7) ? today.value : `${month.value}-01`)
watch(month, value => { selectedDay.value = value === today.value.slice(0, 7) ? today.value : `${value}-01` })

const community = computed({
  get: () => GAME_COMMUNITIES.some(c => c.key === route.query.community) ? String(route.query.community) : 'all',
  set: (value: string) => router.replace({ query: { ...route.query, community: value } }),
})
const { data, status, error, refresh } = await useLazyAsyncData(
  () => `calendar-month-${community.value}-${month.value}`,
  (_app, { signal }) => api<{ month: string, items: CalendarEntry[] }>('/api/calendar/month', { query: { community: community.value, month: month.value }, signal }),
  { watch: [community, month] },
)
const days = computed(() => calendarDays(month.value, status.value === 'success' ? data.value?.items ?? [] : []))
const weeks = computed(() => Array.from(
  { length: Math.ceil((days.value.findLastIndex(day => day.inMonth) + 1) / 7) },
  (_, index) => days.value.slice(index * 7, index * 7 + 7),
))
const selected = computed(() => days.value.find(day => day.date === selectedDay.value))
const selectedMilestones = computed(() => selected.value?.milestones ?? [])
const ongoingItems = computed(() => {
  const changed = new Set(selectedMilestones.value.map(point => point.entry.id))
  return (selected.value?.items ?? []).filter(item => !changed.has(item.id))
    .sort((a, b) => (a.end_at ?? '9999').localeCompare(b.end_at ?? '9999'))
})
const ongoingOpen = ref(false)
const detailsPanel = ref<HTMLElement | null>(null)
watch([selectedDay, community], () => { ongoingOpen.value = false })
type DetailItem = Omit<CalendarMilestone, 'phase'> & { phase: CalendarMilestone['phase'] | 'ongoing' }
const detailGroups = computed(() => [
  { key: 'milestones', items: selectedMilestones.value, collapsible: false },
  { key: 'ongoing', items: ongoingItems.value.map(entry => ({ entry, date: selectedDay.value, at: entry.end_at ?? entry.start_at!, phase: 'ongoing' as const })), collapsible: true },
])
const filters = [
  { key: 'all', label: '全部游戏', color: 'blue' },
  ...GAME_COMMUNITIES.map(c => ({ ...c, label: c.key === 'ai' ? 'AI 快讯' : c.label })),
]
const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const selectedLabel = computed(() => new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' }).format(new Date(`${selectedDay.value}T00:00:00Z`)))
const kindLabels: Record<string, string> = { activity: '活动', maintenance: '更新 / 维护', pool: '卡池', broadcast: '直播', delay: '变更', deadline: '截止' }
const communityMeta = (key?: string) => GAME_COMMUNITIES.find(c => c.key === key)
const itemColor = (item: CalendarEntry) => communityClasses(communityMeta(item.community)?.color ?? 'blue').chip
const itemDot = (item: CalendarEntry) => communityClasses(communityMeta(item.community)?.color ?? 'blue').dot

function pointLabel(point: DetailItem) {
  if (point.phase === 'ongoing') return kindLabels[point.entry.kind] ?? '活动'
  if (point.phase === 'end') return '结束'
  if (point.phase === 'deadline') return '截止'
  if (point.entry.kind === 'maintenance') return '维护'
  if (['broadcast', 'delay'].includes(point.entry.kind)) return kindLabels[point.entry.kind]
  return point.phase === 'single' ? '当天' : '开始'
}
function pointTime(point: DetailItem) {
  return point.entry.all_day ? '全天' : fmtTime(point.at).slice(11)
}
function displayTitle(item: CalendarEntry) {
  return decodeEntities(item.title).replace(/^\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2}\s*/, '')
}

function changeMonth(offset: number) {
  router.replace({ query: { ...route.query, month: shiftCalendarMonth(month.value, offset) } })
}
function selectDate(date: string) {
  selectedDay.value = date
  // 窄屏详情位于月历下方，点选后直接带到详情，避免用户看不到变化。
  if (window.matchMedia('(max-width: 1279px)').matches) {
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    nextTick(() => detailsPanel.value?.scrollIntoView({ behavior, block: 'start' }))
  }
}
function goToToday() {
  selectedDay.value = today.value
  router.replace({ query: { ...route.query, month: today.value.slice(0, 7) } })
}
function timeRange(item: CalendarEntry) {
  const format = (value: string | null) => item.all_day ? fmtTime(value).slice(5, 10) : fmtTime(value).slice(5)
  if (item.kind === 'deadline') return `${format(item.start_at)} 截止`
  if (item.end_at && beijingDay(item.start_at!) === beijingDay(item.end_at)) {
    return item.all_day ? `${format(item.start_at)} 全天` : `${format(item.start_at)} — ${fmtTime(item.end_at).slice(11)}`
  }
  if (item.end_at) return `${format(item.start_at)} — ${format(item.end_at)}`
  return `${format(item.start_at)} 起`
}
</script>

<template>
  <div>
    <SectionNav />
    <header class="mb-5 flex flex-wrap items-center gap-3">
      <div class="wall-heading mr-auto">
        <h1 class="text-2xl font-bold text-ink">活动日历</h1>
      </div>
      <div class="order-last flex w-full min-w-0 items-center gap-1 overflow-x-auto rounded-full bg-chip/65 p-1 [scrollbar-width:none] lg:order-none lg:w-auto" role="group" aria-label="游戏范围">
        <button
          v-for="filter in filters"
          :key="filter.key"
          type="button"
          class="inline-flex h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-full px-3.5 text-[13px] transition-colors sm:h-10"
          :class="community === filter.key ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-card/60 hover:text-ink'"
          :aria-pressed="community === filter.key"
          @click="community = filter.key"
        >
          <span v-if="filter.key !== 'all'" class="size-1.5 rounded-full" :class="communityClasses(filter.color).dot" aria-hidden="true" />
          {{ filter.label }}
        </button>
      </div>
      <button type="button" class="grid size-11 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-chip hover:text-ink" aria-label="刷新日历" :disabled="status === 'pending'" @click="refresh()">
        <RefreshCw :size="17" :class="{ 'animate-spin': status === 'pending' }" aria-hidden="true" />
      </button>
    </header>

    <div class="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <section class="panel min-w-0 overflow-hidden" :aria-label="`${monthLabel}月历`" :aria-busy="status === 'pending'">
        <div class="flex items-center justify-between gap-2 px-3 py-3 sm:px-5">
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 class="num text-xl font-bold text-ink">{{ monthLabel }}</h2>
            <span class="text-[11px] text-muted">北京时间</span>
          </div>
          <nav aria-label="月份切换" class="flex shrink-0 items-center gap-0.5">
            <button type="button" class="grid size-11 place-items-center rounded-[10px] text-ink-2 transition-colors hover:bg-chip" aria-label="上个月" :disabled="month === '1900-01'" @click="changeMonth(-1)"><ChevronLeft :size="18" aria-hidden="true" /></button>
            <button type="button" class="h-11 rounded-[10px] px-2.5 text-[13px] text-ink-2 transition-colors hover:bg-chip" @click="goToToday">今天</button>
            <button type="button" class="grid size-11 place-items-center rounded-[10px] text-ink-2 transition-colors hover:bg-chip" aria-label="下个月" :disabled="month === '2199-12'" @click="changeMonth(1)"><ChevronRight :size="18" aria-hidden="true" /></button>
          </nav>
        </div>
        <p v-if="error" role="alert" class="border-t border-line px-4 py-3 text-sm text-muted">
          日程加载失败，<button type="button" class="link" @click="refresh()">重新加载</button>
        </p>
        <table class="w-full table-fixed border-collapse" :aria-label="`${monthLabel}日期`">
          <thead>
            <tr class="border-y border-line">
              <th v-for="(day, index) in weekdays" :key="day" scope="col" class="py-2.5 text-center text-xs font-medium" :class="index > 4 ? 'text-faint' : 'text-muted'">{{ day }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(week, index) in weeks" :key="index">
              <td v-for="day in week" :key="day.date" class="border-b border-r border-line-soft p-0 align-top last:border-r-0">
                <button
                  type="button"
                  class="relative flex h-[68px] w-full min-w-0 flex-col text-left transition-colors hover:bg-brand/5 focus-visible:z-10 focus-visible:outline-offset-[-2px] sm:h-24"
                  :class="selectedDay === day.date ? 'bg-brand/8 ring-1 ring-inset ring-brand/70' : !day.inMonth ? 'bg-chip/25 opacity-50' : ''"
                  :aria-label="`${day.date}，${day.milestones.length} 项开始或结束的安排${day.date === today ? '，今天' : ''}`"
                  :aria-pressed="selectedDay === day.date"
                  :aria-current="day.date === today ? 'date' : undefined"
                  @click="selectDate(day.date)"
                >
                  <span class="mx-1 mt-1 grid size-7 shrink-0 place-items-center rounded-full text-sm tabular-nums sm:mx-1.5 sm:mt-1.5" :class="day.date === today ? 'bg-brand font-semibold text-white dark:bg-blue-600' : 'text-ink-2'">{{ day.number }}</span>
                  <span class="mt-1 hidden w-full min-w-0 space-y-1 px-2 sm:block">
                    <span v-for="point in day.milestones.slice(0, 2)" :key="`${point.entry.id}-${point.phase}`" class="flex min-w-0 items-center gap-1.5 text-[11px] leading-4" :title="`${communityMeta(point.entry.community)?.label ?? ''} · ${pointLabel(point)} · ${displayTitle(point.entry)}`">
                      <span class="size-1.5 shrink-0 rounded-full" :class="itemDot(point.entry)" aria-hidden="true" />
                      <span class="shrink-0 text-[10px]" :class="['end', 'deadline'].includes(point.phase) ? 'text-hot' : 'text-muted'">{{ pointLabel(point) }}</span>
                      <span class="min-w-0 truncate text-ink-2">{{ displayTitle(point.entry) }}</span>
                    </span>
                    <span v-if="day.milestones.length > 2" class="block text-[10px] text-muted">+{{ day.milestones.length - 2 }} 项</span>
                  </span>
                  <span v-if="day.milestones.length" class="mt-1 flex w-full flex-col items-center gap-1 sm:hidden">
                    <span class="flex gap-1" aria-hidden="true"><span v-for="point in day.milestones.slice(0, 3)" :key="`${point.entry.id}-${point.phase}`" class="size-1 rounded-full" :class="itemDot(point.entry)" /></span>
                    <span class="text-[10px] text-muted">{{ day.milestones.length }} 项</span>
                  </span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section ref="detailsPanel" class="panel flex min-w-0 scroll-mt-28 flex-col overflow-hidden xl:sticky xl:top-[88px] xl:max-h-[calc(100dvh-112px)]" aria-labelledby="selected-date-title">
        <header class="shrink-0 border-b border-line px-5 py-4" aria-live="polite">
          <h2 id="selected-date-title" class="text-lg font-bold text-ink">{{ selectedLabel }}</h2>
          <p class="mt-1 text-xs text-muted">{{ status === 'pending' ? '正在加载…' : `${selectedMilestones.length} 项开始 / 结束 · ${ongoingItems.length} 项持续进行` }}</p>
        </header>
        <div class="min-h-0 overflow-y-auto">
          <p v-if="status === 'pending'" role="status" class="px-5 py-10 text-center text-sm text-muted">正在加载日程…</p>
          <p v-else-if="error" class="px-5 py-8 text-sm text-muted">日程暂时无法显示。</p>
          <template v-else>
            <section v-for="group in detailGroups" :key="group.key">
              <button
                v-if="group.collapsible && group.items.length"
                type="button"
                class="flex min-h-12 w-full items-center justify-between gap-3 border-t border-line bg-chip/30 px-5 py-3 text-left text-[13px] text-ink-2 transition-colors hover:bg-chip/60"
                :aria-expanded="ongoingOpen"
                aria-controls="ongoing-schedules"
                @click="ongoingOpen = !ongoingOpen"
              >
                <span>持续进行 <span class="num ml-1 font-semibold">{{ group.items.length }}</span></span>
                <ChevronDown :size="16" class="transition-transform" :class="{ 'rotate-180': ongoingOpen }" aria-hidden="true" />
              </button>
              <div v-if="!group.collapsible || ongoingOpen" :id="group.collapsible ? 'ongoing-schedules' : undefined" class="divide-y divide-line-soft">
                <article v-for="point in group.items" :key="`${point.entry.id}-${point.phase}`" class="px-5 pb-2 pt-4">
                  <div class="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                    <span v-if="communityMeta(point.entry.community)" class="rounded-md px-1.5 py-0.5 font-medium" :class="itemColor(point.entry)">{{ communityMeta(point.entry.community)!.label }}</span>
                    <span :class="{ 'font-semibold text-hot': ['end', 'deadline'].includes(point.phase) }">{{ pointLabel(point) }}</span>
                    <span v-if="point.entry.delayed" class="text-warn">时间已调整</span>
                    <span v-if="point.phase !== 'ongoing'" class="mono ml-auto text-xs text-ink-2">{{ pointTime(point) }}</span>
                  </div>
                  <NuxtLink :to="`/contents/${point.entry.content_id}`" class="block break-words text-sm font-semibold leading-relaxed text-ink hover:text-brand-ink">{{ displayTitle(point.entry) }}</NuxtLink>
                  <p class="mt-1.5 text-xs leading-relaxed tabular-nums text-muted">{{ timeRange(point.entry) }}</p>
                  <div class="mt-0.5 flex flex-wrap gap-x-5 text-xs">
                    <a v-if="point.entry.url" :href="point.entry.url" target="_blank" rel="noopener noreferrer" class="link inline-flex min-h-11 items-center gap-1">原文<ArrowUpRight :size="12" aria-hidden="true" /></a>
                    <a :href="`/api/calendar/${point.entry.id}.ics`" class="link inline-flex min-h-11 items-center" :aria-label="`将${displayTitle(point.entry)}加入日历`">加入日历</a>
                  </div>
                </article>
                <p v-if="!group.collapsible && !group.items.length" class="px-5 py-10 text-sm leading-relaxed text-muted">这一天没有开始或结束的安排。</p>
              </div>
            </section>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>
