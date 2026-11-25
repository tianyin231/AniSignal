<script setup lang="ts">
import {
  ArrowUpRight,
  CalendarDays,
  CalendarPlus,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Copy,
  EyeOff,
  GanttChart,
  Globe,
  RefreshCw,
  Ticket,
  X,
} from 'lucide-vue-next'
import { communityClasses, communityMeta, useHiddenCommunities } from '~/composables/useGameSection'
import type { CalendarEntry, CodesDto } from '~/composables/usePublicApi'
import {
  CALENDAR_TIMEZONES,
  calendarDays,
  dayInZone,
  formatTimeInZone,
  shiftCalendarMonth,
  shiftIsoDay,
  timezoneMeta,
  type CalendarMilestone,
  type CalendarTimezoneId,
} from '~/utils/calendar'
import { copyText } from '~/utils/clipboard'
import { codeServerLabel, codeSourceBadge, formatCodeExpiry } from '~/utils/calendar-state'
import { ENDING_SOON_MS, KIND_LABELS, fmtRemaining } from '~/utils/schedule'
import { decodeEntities } from '~/utils/text'
import { buildTimeline, effectiveScheduleBounds } from '~/utils/timeline'

useSeoMeta({
  title: '活动日历 · AniSignal',
  description: '跨游戏横向甘特时间轴与月历视图：活动、卡池、版本维护、直播与兑换码一站追踪，支持时区切换与个人打卡。',
})

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()
const { communities } = useGameSection()
const {
  timezone,
  isSynced,
  isCodeClaimed,
  toggleCodeClaimed,
  isScheduleCompleted,
  toggleScheduleCompleted,
  setTimezone,
  completedSchedules,
} = useCalendarState()

// SSR 与水合首帧使用统一时间戳；客户端挂载后定时刷新以维持「现在」竖线准确。
const nowMs = useState('calendar-now-ms', () => Date.now())
let nowTimer: ReturnType<typeof setInterval> | undefined

const today = computed(() => dayInZone(nowMs.value, timezone.value))
const tzInfo = computed(() => timezoneMeta(timezone.value))

// 视图模式：默认时间轴；若历史链接仅带 ?month= 则保留月历视图。
const viewMode = computed<'timeline' | 'month'>({
  get: () => {
    const raw = String(route.query.view || '')
    if (raw === 'month' || raw === 'timeline') return raw
    if (route.query.month) return 'month'
    return 'timeline'
  },
  set: (value) => {
    router.replace({ query: { ...route.query, view: value } })
  },
})

const community = computed({
  get: () => communityMeta(String(route.query.community || '')) ? String(route.query.community) : 'all',
  set: (value: string) => router.replace({ query: { ...route.query, community: value } }),
})

// ── 时间轴参数 ──
const TIMELINE_DAYS_BACK = 3
const spanDays = ref<number>(30)
const timelineOffsetDays = ref<number>(0)
const timelineStartDay = computed(() => shiftIsoDay(today.value, timelineOffsetDays.value - TIMELINE_DAYS_BACK))

// ── 月历参数 ──
const month = computed(() => /^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$/.test(String(route.query.month ?? ''))
  ? String(route.query.month)
  : today.value.slice(0, 7))
const monthLabel = computed(() => `${Number(month.value.slice(0, 4))} 年 ${Number(month.value.slice(5))} 月`)
const selectedDay = ref(month.value === today.value.slice(0, 7) ? today.value : `${month.value}-01`)
watch(month, value => { selectedDay.value = value === today.value.slice(0, 7) ? today.value : `${value}-01` })

// ── 分类与已完成过滤 ──
type KindFilterKey = 'all' | 'activity' | 'pool' | 'maintenance' | 'broadcast' | 'deadline' | 'delay'
const selectedKind = ref<KindFilterKey>('all')
const hideCompleted = ref(false)
const BASE_KIND_FILTERS: Array<{ key: KindFilterKey; label: string }> = [
  { key: 'all', label: '全部分类' },
  { key: 'activity', label: '活动' },
  { key: 'pool', label: '卡池' },
  { key: 'maintenance', label: '更新 / 维护' },
  { key: 'broadcast', label: '直播' },
  { key: 'deadline', label: '截止' },
]

// ── 拉取日程数据（按当前视图仅请求对应端点） ──
interface CalendarPayload {
  items: CalendarEntry[]
  start?: string
  end?: string
  month?: string
}

const querySignature = computed(() => (
  viewMode.value === 'month'
    ? `month-${community.value}-${month.value}`
    : `range-${community.value}-${timelineOffsetDays.value === 0 ? 'default' : timelineStartDay.value}-${spanDays.value}`
))

const { data, status, error, refresh } = await useLazyAsyncData(
  () => `calendar-${querySignature.value}`,
  (_app, { signal }) => {
    if (viewMode.value === 'month') {
      return api<CalendarPayload>('/api/calendar/month', {
        query: { community: community.value, month: month.value },
        signal,
      })
    }
    const query: Record<string, string | number> = {
      community: community.value,
      days: spanDays.value,
      days_back: TIMELINE_DAYS_BACK,
    }
    if (timelineOffsetDays.value !== 0) {
      query.start = timelineStartDay.value
    }
    return api<CalendarPayload>('/api/calendar/range', { query, signal })
  },
  { watch: [querySignature] },
)

// ── 拉取当前范围有效兑换码 ──
const { data: codesData, refresh: refreshCodes } = await useLazyAsyncData(
  () => `calendar-codes-${community.value}`,
  (_app, { signal }) => api<CodesDto>('/api/codes', { query: { community: community.value }, signal }),
  { watch: [community] },
)

const rawItems = computed<CalendarEntry[]>(() => (status.value === 'success' ? data.value?.items ?? [] : []))
const completedIdSet = computed(() => new Set(completedSchedules.value))

const kindCounts = computed<Record<string, number>>(() => {
  const counts: Record<string, number> = { all: rawItems.value.length }
  for (const item of rawItems.value) {
    counts[item.kind] = (counts[item.kind] ?? 0) + 1
  }
  return counts
})

const kindFilters = computed(() => (
  kindCounts.value.delay
    ? [...BASE_KIND_FILTERS, { key: 'delay' as KindFilterKey, label: '变更' }]
    : BASE_KIND_FILTERS
))

const completedInViewCount = computed(() => rawItems.value.filter(item => completedIdSet.value.has(item.id)).length)

const filteredItems = computed<CalendarEntry[]>(() => rawItems.value.filter((item) => {
  if (selectedKind.value !== 'all' && item.kind !== selectedKind.value) return false
  if (hideCompleted.value && completedIdSet.value.has(item.id)) return false
  return true
}))

// ── 甘特时间轴模型与选中项 ──
const communityOrder = computed(() => communities.value.map(c => c.key))
const timelineModel = computed(() => buildTimeline({
  startDay: timelineStartDay.value,
  daysCount: spanDays.value,
  entries: filteredItems.value,
  now: nowMs.value,
  timeZone: timezone.value,
  communityOrder: communityOrder.value,
}))

const selectedTimelineId = ref<number | null>(null)
const selectedTimelineEntry = computed<CalendarEntry | null>(() => {
  if (selectedTimelineId.value === null) return null
  return filteredItems.value.find(item => item.id === selectedTimelineId.value)
    ?? rawItems.value.find(item => item.id === selectedTimelineId.value)
    ?? null
})

watch([community, viewMode], () => { selectedTimelineId.value = null })

function selectTimelineEntry(entry: CalendarEntry) {
  selectedTimelineId.value = entry.id
  if (import.meta.client && window.matchMedia('(max-width: 1279px)').matches) {
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    nextTick(() => detailsPanel.value?.scrollIntoView({ behavior, block: 'start' }))
  }
}

function shiftTimeline(delta: number) {
  timelineOffsetDays.value += delta
}
function resetTimelineToday() {
  timelineOffsetDays.value = 0
}

// 时间轴未选中具体条时，右侧展示「近期重点」（临期结束 / 近期截止 / 最近开启）
const timelineHighlights = computed(() => {
  const now = nowMs.value
  return filteredItems.value
    .map((entry) => {
      const bounds = effectiveScheduleBounds(entry, timezone.value)
      if (!bounds) return null
      const { startMs: start, endMs: end, isPoint } = bounds
      const completed = completedIdSet.value.has(entry.id)
      const isOngoing = !isPoint && end !== null && now >= start && now < end
      const isUpcoming = now < start
      if (!isOngoing && !isUpcoming) return null
      const urgentMs = isOngoing && end !== null ? end - now : start - now
      const endingSoon = (isOngoing || entry.kind === 'deadline') && urgentMs < ENDING_SOON_MS
      const rank = (completed ? 10 : 0) + (endingSoon ? 0 : isOngoing ? 1 : 2)
      const badge = isOngoing && end !== null
        ? `剩 ${fmtRemaining(end - now)}`
        : entry.kind === 'deadline'
          ? `${fmtRemaining(start - now)}后截止`
          : `${fmtRemaining(start - now)}后开始`
      return { entry, rank, urgentMs, endingSoon, badge, completed }
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
    .sort((a, b) => a.rank - b.rank || a.urgentMs - b.urgentMs || a.entry.id - b.entry.id)
    .slice(0, 10)
})

const selectedEntryStatus = computed(() => {
  const entry = selectedTimelineEntry.value
  if (!entry) return null
  const bounds = effectiveScheduleBounds(entry, timezone.value)
  if (!bounds) return null
  const now = nowMs.value
  const { startMs: start, endMs: end, isPoint } = bounds
  if (now < start) {
    const rem = start - now
    return {
      label: entry.kind === 'deadline' ? `${fmtRemaining(rem)}后截止` : `${fmtRemaining(rem)}后开始`,
      urgent: entry.kind === 'deadline' && rem < ENDING_SOON_MS,
      progress: null as number | null,
    }
  }
  if (!isPoint && end !== null && end > start) {
    if (now >= end) return { label: '已结束', urgent: false, progress: 100 }
    const rem = end - now
    return {
      label: `进行中 · 剩 ${fmtRemaining(rem)}`,
      urgent: rem < ENDING_SOON_MS,
      progress: Math.min(100, Math.max(0, Math.round(((now - start) / (end - start)) * 100))),
    }
  }
  return { label: entry.kind === 'deadline' ? '已截止' : '已开启', urgent: false, progress: null as number | null }
})

// ── 月历派生数据 ──
const days = computed(() => calendarDays(month.value, filteredItems.value, timezone.value))
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

// ── 兑换码快捷打卡栏 ──
const hideClaimedCodes = ref(false)
const codesExpanded = ref(false)
const copiedCode = ref('')
let codeCopyTimer: ReturnType<typeof setTimeout> | undefined

const codeRows = computed(() => (codesData.value?.items ?? [])
  .map((item) => {
    const meta = communityMeta(item.community)
    const claimed = isCodeClaimed(item.code, item.community)
    const source = codeSourceBadge(item)
    const server = codeServerLabel(item.server)
    const expiry = formatCodeExpiry(item, timezone.value, nowMs.value)
    return {
      ...item,
      game: meta?.label,
      dot: communityClasses(meta?.color ?? 'blue').dot,
      chip: communityClasses(meta?.color ?? 'blue').chip,
      claimed,
      source,
      server,
      expiry,
    }
  })
  .sort((a, b) => Number(a.claimed) - Number(b.claimed)))

function codeSourceClass(tone: 'official' | 'verified' | 'community') {
  if (tone === 'official') return 'bg-brand-soft text-brand-ink'
  if (tone === 'verified') return 'bg-ok/12 text-ok'
  return 'bg-chip text-muted'
}

const claimedCodesCount = computed(() => codeRows.value.filter(c => c.claimed).length)
const unclaimedCodesCount = computed(() => codeRows.value.length - claimedCodesCount.value)
const filteredCodeRows = computed(() => hideClaimedCodes.value ? codeRows.value.filter(c => !c.claimed) : codeRows.value)
const displayedCodeRows = computed(() => codesExpanded.value ? filteredCodeRows.value : filteredCodeRows.value.slice(0, 6))

async function copyRedeemCode(code: string) {
  if (await copyText(code)) {
    copiedCode.value = code
    clearTimeout(codeCopyTimer)
    codeCopyTimer = setTimeout(() => { if (copiedCode.value === code) copiedCode.value = '' }, 1600)
  }
}

// ── 分区筛选 ──
const { hidden } = useHiddenCommunities()
const filters = computed(() => [
  { key: 'all', label: '全部分区', color: 'blue' },
  ...communities.value.filter(c => !hidden.value.has(c.key) || c.key === community.value)
    .map(c => ({ ...c, label: c.key === 'ai' ? 'AI 快讯' : c.label })),
])

const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const selectedLabel = computed(() => new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' }).format(new Date(`${selectedDay.value}T00:00:00Z`)))
const kindLabels: Record<string, string> = { activity: '活动', maintenance: '更新 / 维护', pool: '卡池', broadcast: '直播', delay: '变更', deadline: '截止' }
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
  return point.entry.all_day ? '全天' : formatTimeInZone(point.at, timezone.value, { timeOnly: true })
}
function displayTitle(item: CalendarEntry) {
  return decodeEntities(item.title).replace(/^\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2}\s*/, '')
}

function changeMonth(offset: number) {
  router.replace({ query: { ...route.query, month: shiftCalendarMonth(month.value, offset) } })
}
function selectDate(date: string) {
  selectedDay.value = date
  if (import.meta.client && window.matchMedia('(max-width: 1279px)').matches) {
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    nextTick(() => detailsPanel.value?.scrollIntoView({ behavior, block: 'start' }))
  }
}
function goToToday() {
  selectedDay.value = today.value
  router.replace({ query: { ...route.query, month: today.value.slice(0, 7) } })
}
function timeRange(item: CalendarEntry) {
  const format = (value: string | null) => formatTimeInZone(value, timezone.value, { dateOnly: item.all_day })
  if (item.kind === 'deadline') return `${format(item.start_at)} 截止`
  if (item.end_at && dayInZone(item.start_at!, timezone.value) === dayInZone(item.end_at, timezone.value)) {
    return item.all_day
      ? `${format(item.start_at)} 全天`
      : `${format(item.start_at)} — ${formatTimeInZone(item.end_at, timezone.value, { timeOnly: true })}`
  }
  if (item.end_at) return `${format(item.start_at)} — ${format(item.end_at)}`
  return `${format(item.start_at)} 起`
}

function onTimezoneChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value as CalendarTimezoneId
  setTimezone(value)
}

function refreshAll() {
  void refresh()
  void refreshCodes()
}

// ── 日历订阅条 ──
const subscribeOpen = ref(false)
const icsCopied = ref(false)
const siteOrigin = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined
onMounted(() => {
  siteOrigin.value = window.location.origin
  nowMs.value = Date.now()
  nowTimer = setInterval(() => { nowMs.value = Date.now() }, 60_000)
})
onBeforeUnmount(() => {
  clearTimeout(copyTimer)
  clearTimeout(codeCopyTimer)
  clearInterval(nowTimer)
})

const icsPath = computed(() => `/calendar.ics?community=${community.value}`)
const icsHttpUrl = computed(() => `${siteOrigin.value}${icsPath.value}`)
const webcalUrl = computed(() => {
  if (!siteOrigin.value) return icsPath.value
  return `${siteOrigin.value.replace(/^https?:/, 'webcal:')}${icsPath.value}`
})
const activeFilterLabel = computed(() => filters.value.find(f => f.key === community.value)?.label ?? '全部分区')
async function copyIcsUrl() {
  if (await copyText(icsHttpUrl.value || icsPath.value)) {
    icsCopied.value = true
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { icsCopied.value = false }, 2000)
  }
}
</script>

<template>
  <div>
    <SectionNav />

    <!-- 顶栏：标题 + 视图切换 + 时区/订阅/刷新；游戏分区独立整行展示不截断 -->
    <header class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap items-center gap-3">
        <div class="wall-heading">
          <h1 class="text-2xl font-bold text-ink">活动日历</h1>
        </div>
        <div class="inline-flex rounded-full bg-chip/75 p-1" role="group" aria-label="日历视图切换">
          <button
            type="button"
            class="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors"
            :class="viewMode === 'timeline' ? 'bg-ink text-card shadow-2xs' : 'text-ink-2 hover:text-ink'"
            :aria-pressed="viewMode === 'timeline'"
            @click="viewMode = 'timeline'"
          >
            <GanttChart :size="14" aria-hidden="true" />
            时间轴
          </button>
          <button
            type="button"
            class="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors"
            :class="viewMode === 'month' ? 'bg-ink text-card shadow-2xs' : 'text-ink-2 hover:text-ink'"
            :aria-pressed="viewMode === 'month'"
            @click="viewMode = 'month'"
          >
            <CalendarDays :size="14" aria-hidden="true" />
            月历
          </button>
        </div>
      </div>

      <!-- 右侧操作组：时区切换 + 订阅 + 刷新 -->
      <div class="flex shrink-0 flex-wrap items-center gap-1.5">
        <label class="relative inline-flex h-10 shrink-0 items-center whitespace-nowrap rounded-full border border-line bg-card pl-3 pr-2.5 text-xs text-ink-2 transition-colors hover:bg-chip">
          <Globe :size="14" class="mr-1.5 shrink-0 text-muted" aria-hidden="true" />
          <span class="sr-only">选择显示时区</span>
          <select
            :value="timezone"
            aria-label="显示时区"
            class="h-full cursor-pointer whitespace-nowrap bg-transparent pr-1 text-xs font-medium text-ink focus:outline-none"
            @change="onTimezoneChange"
          >
            <option v-for="opt in CALENDAR_TIMEZONES" :key="opt.id" :value="opt.id" class="bg-card text-ink">
              {{ opt.label }}
            </option>
          </select>
        </label>
        <button
          type="button"
          class="inline-flex h-10 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-card px-3 text-[13px] font-medium text-ink-2 transition-colors hover:bg-chip hover:text-ink"
          :aria-expanded="subscribeOpen"
          aria-controls="calendar-subscribe-bar"
          @click="subscribeOpen = !subscribeOpen"
        >
          <CalendarPlus :size="15" aria-hidden="true" />
          <span class="hidden sm:inline">订阅日历</span>
        </button>
        <button
          type="button"
          class="grid size-10 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-chip hover:text-ink"
          aria-label="刷新日历"
          :disabled="status === 'pending'"
          @click="refreshAll()"
        >
          <RefreshCw :size="17" :class="{ 'animate-spin': status === 'pending' }" aria-hidden="true" />
        </button>
      </div>
    </header>

    <!-- 游戏分区筛选：桌面端自然换行展示全部分区，移动端单行横滑 -->
    <div class="mb-3 flex min-w-0 items-center gap-1 overflow-x-auto rounded-2xl bg-chip/65 p-1 [scrollbar-width:none] sm:flex-wrap sm:overflow-visible" role="group" aria-label="游戏范围">
      <button
        v-for="filter in filters"
        :key="filter.key"
        type="button"
        class="inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3 text-[13px] transition-colors"
        :class="community === filter.key ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-card/60 hover:text-ink'"
        :aria-pressed="community === filter.key"
        @click="community = filter.key"
      >
        <span v-if="filter.key !== 'all'" class="size-1.5 rounded-full" :class="communityClasses(filter.color).dot" aria-hidden="true" />
        {{ filter.label }}
      </button>
    </div>

    <!-- 分类筛选与个人打卡状态栏 -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2.5">
      <div class="flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label="日程类型筛选">
        <button
          v-for="item in kindFilters"
          :key="item.key"
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors"
          :class="selectedKind === item.key
            ? 'border-brand/50 bg-brand-soft text-brand-ink'
            : 'border-line bg-card text-ink-2 hover:bg-chip hover:text-ink'"
          :aria-pressed="selectedKind === item.key"
          @click="selectedKind = item.key"
        >
          <span>{{ item.label }}</span>
          <span v-if="kindCounts[item.key]" class="num text-[11px] opacity-70">{{ kindCounts[item.key] }}</span>
        </button>
      </div>
      <div class="flex flex-wrap items-center gap-2.5 text-xs">
        <button
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors"
          :class="hideCompleted
            ? 'border-ok/50 bg-ok/10 text-ok'
            : 'border-line bg-card text-muted hover:bg-chip hover:text-ink'"
          :aria-pressed="hideCompleted"
          @click="hideCompleted = !hideCompleted"
        >
          <EyeOff :size="13" aria-hidden="true" />
          隐藏已完成
          <span v-if="completedInViewCount > 0" class="num font-semibold">({{ completedInViewCount }})</span>
        </button>
        <span class="hidden text-[11px] text-faint sm:inline">
          {{ isSynced ? '打卡与时区已同步账号' : '本地保存 · 登录后跨端同步' }}
        </span>
      </div>
    </div>

    <!-- 订阅日历折叠栏 -->
    <section v-if="subscribeOpen" id="calendar-subscribe-bar" class="panel mb-4 flex flex-wrap items-center gap-2.5 px-4 py-3" aria-label="日历订阅地址">
      <span class="shrink-0 text-xs font-semibold text-ink">{{ activeFilterLabel }} · .ics</span>
      <input
        :value="icsHttpUrl || icsPath"
        readonly
        aria-label="日历订阅地址"
        class="input mono h-9 min-w-[200px] flex-1 text-xs"
        @focus="($event.target as HTMLInputElement).select()"
      >
      <div class="flex shrink-0 items-center gap-2">
        <button type="button" class="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-line px-3 text-xs font-semibold text-ink-2 transition-colors hover:bg-chip hover:text-ink" @click="copyIcsUrl">
          <Check v-if="icsCopied" :size="14" /><Copy v-else :size="14" />{{ icsCopied ? '已复制' : '复制地址' }}
        </button>
        <a :href="webcalUrl" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3 text-xs">
          <CalendarPlus :size="14" aria-hidden="true" />系统日历打开
        </a>
      </div>
    </section>

    <!-- 可用兑换码快捷打卡栏 -->
    <section v-if="codeRows.length" class="panel mb-5 px-4 py-3 sm:px-5" aria-label="可用兑换码">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <Ticket :size="15" class="text-brand-ink" aria-hidden="true" />
          <h2 class="text-sm font-bold text-ink">可用兑换码</h2>
          <span class="rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-semibold tabular-nums text-brand-ink">
            {{ unclaimedCodesCount > 0 ? `${unclaimedCodesCount} 个待领` : '已全部标记' }}
          </span>
          <span v-if="claimedCodesCount > 0" class="text-[11px] tabular-nums text-muted">
            已领 {{ claimedCodesCount }} / {{ codeRows.length }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="claimedCodesCount > 0"
            type="button"
            class="rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors hover:bg-chip hover:text-ink"
            :aria-pressed="hideClaimedCodes"
            @click="hideClaimedCodes = !hideClaimedCodes"
          >
            {{ hideClaimedCodes ? `显示已领 (${claimedCodesCount})` : '隐藏已领' }}
          </button>
          <button
            v-if="filteredCodeRows.length > 6"
            type="button"
            class="rounded-md px-2 py-1 text-xs font-medium text-brand-ink transition-colors hover:bg-brand-soft"
            @click="codesExpanded = !codesExpanded"
          >
            {{ codesExpanded ? '收起' : `展开全部 (${filteredCodeRows.length})` }}
          </button>
        </div>
      </div>

      <p v-if="!displayedCodeRows.length" class="mt-2.5 text-xs text-muted">
        当前范围的有效兑换码已全部标记已领。
      </p>
      <div v-else class="mt-2.5 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <div
          v-for="item in displayedCodeRows"
          :key="`${item.community}-${item.code}`"
          class="flex flex-col gap-1.5 rounded-xl border px-3 py-2.5 transition-all"
          :class="item.claimed
            ? 'border-line-soft bg-chip/35 opacity-60'
            : 'border-line bg-card'"
        >
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="mono inline-flex h-8 min-w-0 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold transition-colors"
              :class="item.claimed
                ? 'bg-chip text-muted line-through'
                : 'bg-brand-soft text-brand-ink hover:bg-brand/20'"
              :aria-label="copiedCode === item.code ? '已复制' : `复制兑换码 ${item.code}`"
              @click="copyRedeemCode(item.code)"
            >
              <span class="truncate">{{ item.code }}</span>
              <Check v-if="copiedCode === item.code" :size="13" class="shrink-0" aria-hidden="true" />
              <Copy v-else :size="12" class="shrink-0 opacity-65" aria-hidden="true" />
            </button>

            <button
              type="button"
              class="ml-auto inline-flex h-8 shrink-0 items-center gap-1 rounded-full border px-2.5 text-[11px] font-medium transition-colors"
              :class="item.claimed
                ? 'border-ok/40 bg-ok/10 text-ok'
                : 'border-line text-muted hover:border-brand/40 hover:bg-chip hover:text-ink'"
              :aria-pressed="item.claimed"
              :aria-label="item.claimed ? `取消 ${item.code} 已领标记` : `标记 ${item.code} 为已领`"
              @click="toggleCodeClaimed(item.code, item.community)"
            >
              <CheckCircle2 v-if="item.claimed" :size="13" aria-hidden="true" />
              <Circle v-else :size="13" aria-hidden="true" />
              <span>{{ item.claimed ? '已领' : '标记已领' }}</span>
            </button>

            <a
              v-if="item.url"
              :href="item.url"
              target="_blank"
              rel="noopener noreferrer"
              class="grid size-7 shrink-0 place-items-center rounded-md text-muted transition-colors hover:bg-chip hover:text-ink"
              :aria-label="`查看兑换码 ${item.code} 原文`"
            >
              <ArrowUpRight :size="13" aria-hidden="true" />
            </a>
          </div>

          <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
            <span v-if="community === 'all' && item.game" class="rounded px-1.5 py-0.5 text-[10px] font-semibold" :class="item.chip">
              {{ item.game }}
            </span>
            <span class="rounded px-1.5 py-0.5 text-[10px] font-semibold" :class="codeSourceClass(item.source.tone)">
              {{ item.source.label }}
            </span>
            <span v-if="item.server" class="rounded border border-line px-1.5 py-0.5 text-[10px] font-medium text-ink-2">
              {{ item.server }}
            </span>
            <span v-if="item.reward" class="truncate text-ink-2">{{ item.reward }}</span>
            <span class="tabular-nums" :class="{ 'font-semibold text-hot': item.expiry.urgent && !item.claimed }">
              {{ item.expiry.text }}
            </span>
          </div>
        </div>
      </div>
    </section>

    <!-- 主内容区：横向甘特时间轴视图 -->
    <div v-if="viewMode === 'timeline'" class="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <CalendarTimeline
        :model="timelineModel"
        :selected-id="selectedTimelineId"
        :completed-ids="completedIdSet"
        :time-zone="timezone"
        :span-days="spanDays"
        :loading="status === 'pending'"
        :error="error"
        @select="selectTimelineEntry"
        @shift="shiftTimeline"
        @today="resetTimelineToday"
        @update:span-days="spanDays = $event"
        @refresh="refresh()"
      />

      <!-- 时间轴右侧详情 / 近期重点面板 -->
      <section ref="detailsPanel" class="panel flex min-w-0 scroll-mt-28 flex-col overflow-hidden xl:sticky xl:top-[88px] xl:max-h-[calc(100dvh-112px)]" aria-live="polite">
        <template v-if="selectedTimelineEntry">
          <header class="flex shrink-0 items-center justify-between gap-2 border-b border-line px-5 py-3.5">
            <h2 class="text-base font-bold text-ink">日程详情</h2>
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-xs text-muted transition-colors hover:bg-chip hover:text-ink"
              @click="selectedTimelineId = null"
            >
              <X :size="14" aria-hidden="true" />
              返回重点
            </button>
          </header>
          <div class="min-h-0 overflow-y-auto px-5 py-4">
            <div class="mb-2.5 flex flex-wrap items-center gap-2 text-[11px] text-muted">
              <span v-if="communityMeta(selectedTimelineEntry.community)" class="rounded-md px-1.5 py-0.5 font-semibold" :class="itemColor(selectedTimelineEntry)">
                {{ communityMeta(selectedTimelineEntry.community)!.label }}
              </span>
              <span class="rounded-md bg-chip px-1.5 py-0.5 font-medium text-ink-2">
                {{ kindLabels[selectedTimelineEntry.kind] ?? '活动' }}
              </span>
              <span v-if="selectedTimelineEntry.delayed" class="font-medium text-warn">时间已调整</span>
            </div>

            <NuxtLink
              :to="`/contents/${selectedTimelineEntry.content_id}`"
              class="block break-words text-base font-bold leading-snug text-ink hover:text-brand-ink"
              :class="{ 'line-through opacity-65': isScheduleCompleted(selectedTimelineEntry.id) }"
            >
              {{ displayTitle(selectedTimelineEntry) }}
            </NuxtLink>

            <div class="mt-3 rounded-xl border border-line-soft bg-chip/35 p-3">
              <p class="text-xs font-medium tabular-nums text-ink-2">{{ timeRange(selectedTimelineEntry) }}</p>
              <p class="mt-1 text-[11px] text-muted">时区：{{ tzInfo.label }}</p>
              <div v-if="selectedEntryStatus" class="mt-2.5">
                <div class="flex items-center justify-between text-xs">
                  <span class="font-semibold" :class="selectedEntryStatus.urgent ? 'text-hot' : 'text-ink-2'">
                    {{ selectedEntryStatus.label }}
                  </span>
                  <span v-if="selectedEntryStatus.progress !== null" class="num text-[11px] text-muted">
                    {{ selectedEntryStatus.progress }}%
                  </span>
                </div>
                <div v-if="selectedEntryStatus.progress !== null" class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
                  <div
                    class="h-full rounded-full transition-all"
                    :class="selectedEntryStatus.urgent ? 'bg-hot' : itemDot(selectedTimelineEntry)"
                    :style="{ width: `${selectedEntryStatus.progress}%` }"
                  />
                </div>
              </div>
            </div>

            <div class="mt-4 flex flex-col gap-2">
              <button
                type="button"
                class="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border text-sm font-semibold transition-colors"
                :class="isScheduleCompleted(selectedTimelineEntry.id)
                  ? 'border-ok/40 bg-ok/10 text-ok hover:bg-ok/15'
                  : 'border-line bg-card text-ink hover:border-brand/50 hover:bg-chip'"
                :aria-pressed="isScheduleCompleted(selectedTimelineEntry.id)"
                @click="toggleScheduleCompleted(selectedTimelineEntry.id)"
              >
                <CheckCircle2 v-if="isScheduleCompleted(selectedTimelineEntry.id)" :size="16" aria-hidden="true" />
                <Circle v-else :size="16" aria-hidden="true" />
                {{ isScheduleCompleted(selectedTimelineEntry.id) ? '已完成（点击取消）' : '标记为已完成' }}
              </button>

              <div class="mt-1 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
                <NuxtLink :to="`/contents/${selectedTimelineEntry.content_id}`" class="link inline-flex min-h-10 items-center">
                  站内详情
                </NuxtLink>
                <a v-if="selectedTimelineEntry.url" :href="selectedTimelineEntry.url" target="_blank" rel="noopener noreferrer" class="link inline-flex min-h-10 items-center gap-1">
                  原文<ArrowUpRight :size="12" aria-hidden="true" />
                </a>
                <a :href="`/api/calendar/${selectedTimelineEntry.id}.ics`" class="link inline-flex min-h-10 items-center">
                  加入日历 (.ics)
                </a>
              </div>
            </div>
          </div>
        </template>

        <template v-else>
          <header class="shrink-0 border-b border-line px-5 py-4">
            <h2 class="text-base font-bold text-ink">近期重点与临期提醒</h2>
            <p class="mt-1 text-xs text-muted">
              {{ status === 'pending' ? '正在整理日程…' : `窗口内 ${timelineHighlights.length} 项进行中 / 即将开启 · ${tzInfo.shortLabel}` }}
            </p>
          </header>
          <div class="min-h-0 overflow-y-auto divide-y divide-line-soft">
            <p v-if="status === 'pending'" role="status" class="px-5 py-10 text-center text-sm text-muted">正在加载日程…</p>
            <p v-else-if="!timelineHighlights.length" class="px-5 py-10 text-sm text-muted">当前筛选窗口内暂无进行中或即将开始的日程。</p>
            <article
              v-for="item in timelineHighlights"
              :key="item.entry.id"
              class="px-5 py-3.5 transition-colors hover:bg-chip/30"
              :class="{ 'opacity-55': item.completed }"
            >
              <div class="mb-1.5 flex items-center gap-2 text-[11px] text-muted">
                <span v-if="communityMeta(item.entry.community)" class="rounded px-1.5 py-0.5 font-semibold" :class="itemColor(item.entry)">
                  {{ communityMeta(item.entry.community)!.label }}
                </span>
                <span>{{ KIND_LABELS[item.entry.kind] ?? '活动' }}</span>
                <span class="ml-auto font-semibold tabular-nums" :class="item.endingSoon && !item.completed ? 'text-hot' : 'text-ink-2'">
                  {{ item.badge }}
                </span>
              </div>
              <div class="flex items-start gap-2">
                <button
                  type="button"
                  class="min-w-0 flex-1 text-left text-sm font-semibold leading-snug text-ink hover:text-brand-ink"
                  :class="{ 'line-through': item.completed }"
                  @click="selectTimelineEntry(item.entry)"
                >
                  {{ displayTitle(item.entry) }}
                </button>
                <button
                  type="button"
                  class="grid size-8 shrink-0 place-items-center rounded-full border transition-colors"
                  :class="item.completed
                    ? 'border-ok/40 bg-ok/10 text-ok'
                    : 'border-line text-muted hover:border-brand/40 hover:bg-chip hover:text-ink'"
                  :aria-pressed="item.completed"
                  :aria-label="item.completed ? `取消 ${displayTitle(item.entry)} 已完成标记` : `标记 ${displayTitle(item.entry)} 为已完成`"
                  @click="toggleScheduleCompleted(item.entry.id)"
                >
                  <CheckCircle2 v-if="item.completed" :size="15" aria-hidden="true" />
                  <Circle v-else :size="15" aria-hidden="true" />
                </button>
              </div>
              <p class="mt-1 text-[11px] tabular-nums text-muted">{{ timeRange(item.entry) }}</p>
            </article>
          </div>
        </template>
      </section>
    </div>

    <!-- 主内容区：经典月历视图（同样支持时区切换、分类过滤与已完成打卡） -->
    <div v-else class="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <section class="panel min-w-0 overflow-hidden" :aria-label="`${monthLabel}月历`" :aria-busy="status === 'pending'">
        <div class="flex items-center justify-between gap-2 px-3 py-3 sm:px-5">
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 class="num text-xl font-bold text-ink">{{ monthLabel }}</h2>
            <span class="rounded-md bg-chip px-1.5 py-0.5 text-[11px] font-medium text-muted">{{ tzInfo.shortLabel }}</span>
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
                    <span
                      v-for="point in day.milestones.slice(0, 2)"
                      :key="`${point.entry.id}-${point.phase}`"
                      class="flex min-w-0 items-center gap-1.5 text-[11px] leading-4"
                      :class="{ 'opacity-45 line-through': isScheduleCompleted(point.entry.id) }"
                      :title="`${communityMeta(point.entry.community)?.label ?? ''} · ${pointLabel(point)} · ${displayTitle(point.entry)}`"
                    >
                      <Check v-if="isScheduleCompleted(point.entry.id)" :size="10" class="shrink-0 text-ok" aria-hidden="true" />
                      <span v-else class="size-1.5 shrink-0 rounded-full" :class="itemDot(point.entry)" aria-hidden="true" />
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
                <article
                  v-for="point in group.items"
                  :key="`${point.entry.id}-${point.phase}`"
                  class="px-5 pb-2 pt-4 transition-opacity"
                  :class="{ 'opacity-60': isScheduleCompleted(point.entry.id) }"
                >
                  <div class="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                    <span v-if="communityMeta(point.entry.community)" class="rounded-md px-1.5 py-0.5 font-medium" :class="itemColor(point.entry)">{{ communityMeta(point.entry.community)!.label }}</span>
                    <span :class="{ 'font-semibold text-hot': ['end', 'deadline'].includes(point.phase) }">{{ pointLabel(point) }}</span>
                    <span v-if="point.entry.delayed" class="text-warn">时间已调整</span>
                    <span v-if="point.phase !== 'ongoing'" class="mono ml-auto text-xs text-ink-2">{{ pointTime(point) }}</span>
                  </div>
                  <NuxtLink
                    :to="`/contents/${point.entry.content_id}`"
                    class="block break-words text-sm font-semibold leading-relaxed text-ink hover:text-brand-ink"
                    :class="{ 'line-through': isScheduleCompleted(point.entry.id) }"
                  >
                    {{ displayTitle(point.entry) }}
                  </NuxtLink>
                  <p class="mt-1.5 text-xs leading-relaxed tabular-nums text-muted">{{ timeRange(point.entry) }}</p>
                  <div class="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                    <button
                      type="button"
                      class="inline-flex h-8 items-center gap-1 rounded-full border px-2.5 text-[11px] font-medium transition-colors"
                      :class="isScheduleCompleted(point.entry.id)
                        ? 'border-ok/40 bg-ok/10 text-ok'
                        : 'border-line text-muted hover:border-brand/40 hover:bg-chip hover:text-ink'"
                      :aria-pressed="isScheduleCompleted(point.entry.id)"
                      @click="toggleScheduleCompleted(point.entry.id)"
                    >
                      <CheckCircle2 v-if="isScheduleCompleted(point.entry.id)" :size="13" aria-hidden="true" />
                      <Circle v-else :size="13" aria-hidden="true" />
                      {{ isScheduleCompleted(point.entry.id) ? '已完成' : '标记完成' }}
                    </button>
                    <a v-if="point.entry.url" :href="point.entry.url" target="_blank" rel="noopener noreferrer" class="link inline-flex min-h-10 items-center gap-1">原文<ArrowUpRight :size="12" aria-hidden="true" /></a>
                    <a :href="`/api/calendar/${point.entry.id}.ics`" class="link inline-flex min-h-10 items-center" :aria-label="`将${displayTitle(point.entry)}加入日历`">加入日历</a>
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
