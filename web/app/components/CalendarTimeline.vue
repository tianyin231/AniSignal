<script setup lang="ts">
import { Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp } from 'lucide-vue-next'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import type { CalendarEntry } from '~/composables/usePublicApi'
import {
  formatTimeInZone,
  timezoneMeta,
  type CalendarTimezoneId,
} from '~/utils/calendar'
import { KIND_LABELS } from '~/utils/schedule'
import { decodeEntities } from '~/utils/text'
import type { TimelineBar, TimelineLane, TimelineModel } from '~/utils/timeline'

const props = defineProps<{
  model: TimelineModel
  selectedId: number | null
  completedIds: Set<number>
  timeZone: CalendarTimezoneId
  spanDays: number
  loading: boolean
  error: unknown
}>()

const emit = defineEmits<{
  select: [entry: CalendarEntry]
  shift: [deltaDays: number]
  today: []
  'update:spanDays': [days: number]
  refresh: []
}>()

const TRACK_HEIGHT = 36
const LANE_PAD_Y = 10
const MAX_COLLAPSED_TRACKS = 4

const expandedLanes = ref<Set<string>>(new Set())

// 若右侧重点列表点选了被折叠轨道里的日程，自动展开对应游戏泳道以便高亮定位。
watch(() => props.selectedId, (id) => {
  if (id === null) return
  for (const lane of props.model.lanes) {
    const bar = lane.bars.find(b => b.entry.id === id)
    if (bar && bar.track >= MAX_COLLAPSED_TRACKS && !expandedLanes.value.has(lane.community)) {
      const next = new Set(expandedLanes.value)
      next.add(lane.community)
      expandedLanes.value = next
      break
    }
  }
})

function isLaneFoldable(lane: TimelineLane) {
  return props.model.lanes.length > 1 && lane.trackCount > MAX_COLLAPSED_TRACKS
}

function isLaneExpanded(lane: TimelineLane) {
  return !isLaneFoldable(lane) || expandedLanes.value.has(lane.community)
}

function toggleLane(community: string) {
  const next = new Set(expandedLanes.value)
  if (next.has(community)) next.delete(community)
  else next.add(community)
  expandedLanes.value = next
}

const foldableLaneKeys = computed(() => props.model.lanes.filter(isLaneFoldable).map(l => l.community))
const allFoldablesExpanded = computed(() =>
  foldableLaneKeys.value.length > 0 && foldableLaneKeys.value.every(key => expandedLanes.value.has(key)),
)

function toggleAllLanes() {
  if (allFoldablesExpanded.value) {
    expandedLanes.value = new Set()
  } else {
    expandedLanes.value = new Set(foldableLaneKeys.value)
  }
}

function visibleBars(lane: TimelineLane) {
  return isLaneExpanded(lane) ? lane.bars : lane.bars.filter(b => b.track < MAX_COLLAPSED_TRACKS)
}

function hiddenBarCount(lane: TimelineLane) {
  return lane.bars.length - visibleBars(lane).length
}

function visibleTrackCount(lane: TimelineLane) {
  return isLaneExpanded(lane) ? lane.trackCount : Math.min(lane.trackCount, MAX_COLLAPSED_TRACKS)
}

const rangeLabel = computed(() => {
  const fmt = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`
  const startYear = props.model.startDay.slice(0, 4)
  const endYear = props.model.endDay.slice(0, 4)
  return startYear === endYear
    ? `${startYear} · ${fmt(props.model.startDay)} — ${fmt(props.model.endDay)}`
    : `${startYear}/${fmt(props.model.startDay)} — ${endYear}/${fmt(props.model.endDay)}`
})

const tzShortLabel = computed(() => timezoneMeta(props.timeZone).shortLabel)
const shiftStep = computed(() => (props.spanDays <= 14 ? 7 : 14))
const minGridWidth = computed(() => (props.spanDays <= 14 ? 'min-w-[780px]' : 'min-w-[1040px]'))

function displayTitle(entry: CalendarEntry) {
  return decodeEntities(entry.title).replace(/^\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{2}\s*/, '')
}

function laneMeta(key: string) {
  const meta = communityMeta(key)
  return {
    label: meta?.label ?? (key === 'other' ? '其他' : key),
    classes: communityClasses(meta?.color ?? 'blue'),
  }
}

function barClasses(community: string | undefined) {
  return communityClasses(communityMeta(community)?.color ?? 'blue')
}

function barTooltip(bar: TimelineBar) {
  const meta = communityMeta(bar.entry.community)
  const kind = KIND_LABELS[bar.entry.kind] ?? '活动'
  const start = formatTimeInZone(bar.entry.start_at, props.timeZone, { dateOnly: bar.entry.all_day })
  const end = bar.entry.end_at
    ? formatTimeInZone(bar.entry.end_at, props.timeZone, { dateOnly: bar.entry.all_day })
    : null
  const range = bar.entry.kind === 'deadline'
    ? `${start} 截止`
    : end
      ? `${start} — ${end}`
      : `${start} 起`
  return `${meta ? `${meta.label} · ` : ''}[${kind}] ${displayTitle(bar.entry)}\n${range}${bar.badgeText ? `（${bar.badgeText}）` : ''}`
}

function isCompleted(entry: CalendarEntry) {
  return props.completedIds.has(entry.id)
}
</script>

<template>
  <section class="panel min-w-0 overflow-hidden" aria-label="跨游戏横向活动时间轴" :aria-busy="loading">
    <div class="flex flex-wrap items-center justify-between gap-2 px-3 py-3 sm:px-5">
      <div class="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <h2 class="num text-lg font-bold text-ink sm:text-xl">{{ rangeLabel }}</h2>
        <span class="rounded-md bg-chip px-1.5 py-0.5 text-[11px] font-medium text-muted">{{ tzShortLabel }}</span>
        <span class="text-xs text-muted">
          {{ loading ? '加载中…' : `共 ${model.totalBars} 项` }}
        </span>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <button
          v-if="foldableLaneKeys.length > 0"
          type="button"
          class="inline-flex h-9 items-center gap-1 rounded-[10px] border border-line bg-card px-2.5 text-xs font-medium text-ink-2 transition-colors hover:bg-chip hover:text-ink"
          @click="toggleAllLanes"
        >
          <ChevronUp v-if="allFoldablesExpanded" :size="14" aria-hidden="true" />
          <ChevronDown v-else :size="14" aria-hidden="true" />
          {{ allFoldablesExpanded ? '折叠长泳道' : '展开全部泳道' }}
        </button>
        <div class="inline-flex rounded-[10px] bg-chip/70 p-0.5 text-xs" role="group" aria-label="时间跨度">
          <button
            v-for="opt in [14, 30]"
            :key="opt"
            type="button"
            class="h-9 rounded-[8px] px-2.5 font-medium transition-colors"
            :class="spanDays === opt ? 'bg-card text-ink shadow-xs' : 'text-muted hover:text-ink'"
            :aria-pressed="spanDays === opt"
            @click="emit('update:spanDays', opt)"
          >
            {{ opt }} 天
          </button>
        </div>
        <nav aria-label="时间窗口切换" class="flex items-center gap-0.5">
          <button
            type="button"
            class="grid size-10 place-items-center rounded-[10px] text-ink-2 transition-colors hover:bg-chip"
            :aria-label="`向前 ${shiftStep} 天`"
            @click="emit('shift', -shiftStep)"
          >
            <ChevronLeft :size="18" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="h-10 rounded-[10px] px-2.5 text-[13px] font-medium text-ink-2 transition-colors hover:bg-chip"
            @click="emit('today')"
          >
            回到今天
          </button>
          <button
            type="button"
            class="grid size-10 place-items-center rounded-[10px] text-ink-2 transition-colors hover:bg-chip"
            :aria-label="`向后 ${shiftStep} 天`"
            @click="emit('shift', shiftStep)"
          >
            <ChevronRight :size="18" aria-hidden="true" />
          </button>
        </nav>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-y border-line-soft bg-chip/25 px-4 py-2 text-[11px] text-muted sm:px-5">
      <span class="inline-flex items-center gap-1.5">
        <span class="h-2.5 w-5 rounded-sm bg-brand-soft ring-1 ring-inset ring-brand/40" aria-hidden="true" />
        区间日程（底边为进度）
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="size-2 rotate-45 bg-brand" aria-hidden="true" />
        直播 / 截止 / 节点
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="h-2.5 w-2.5 rounded-full bg-hot/80" aria-hidden="true" />
        4 天内结束 / 临期
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="inline-grid size-3.5 place-items-center rounded-full bg-ok/15 text-ok" aria-hidden="true">
          <Check :size="10" />
        </span>
        已完成
      </span>
      <span class="ml-auto hidden text-[11px] text-faint sm:inline">点击任意日程条查看详情与打卡</span>
    </div>

    <p v-if="error" role="alert" class="px-5 py-6 text-sm text-muted">
      时间轴加载失败，<button type="button" class="link" @click="emit('refresh')">重新加载</button>
    </p>

    <div v-else class="overflow-x-auto">
      <div :class="minGridWidth">
        <!-- 日期刻度头 -->
        <div class="flex border-b border-line bg-card">
          <div class="sticky left-0 z-40 flex w-[116px] shrink-0 items-center border-r border-line bg-card px-3 py-2 text-xs font-semibold text-muted sm:w-[148px] sm:px-4">
            游戏分区
          </div>
          <div class="relative flex-1 overflow-hidden">
            <div
              class="grid"
              :style="{ gridTemplateColumns: `repeat(${model.daysCount}, minmax(0, 1fr))` }"
            >
              <div
                v-for="day in model.axisDays"
                :key="day.date"
                class="relative flex flex-col items-center justify-end border-r border-line-soft/70 pb-1.5 pt-5 text-center last:border-r-0"
                :class="[
                  day.isToday ? 'bg-brand/[0.07]' : day.isWeekend ? 'bg-chip/35' : '',
                  day.isMonthStart ? 'border-l border-l-line' : '',
                ]"
              >
                <span
                  v-if="day.isMonthStart"
                  class="absolute left-1 top-1 whitespace-nowrap text-[10px] font-semibold leading-none text-brand-ink"
                >
                  {{ day.monthLabel }}
                </span>
                <span
                  class="num grid size-6 place-items-center rounded-full text-xs leading-none"
                  :class="day.isToday ? 'bg-brand font-bold text-white dark:bg-blue-600' : 'font-medium text-ink-2'"
                >
                  {{ day.dayNum }}
                </span>
                <span
                  class="mt-0.5 text-[10px] leading-none"
                  :class="day.isToday ? 'font-semibold text-brand-ink' : day.isWeekend ? 'text-faint' : 'text-muted'"
                >
                  {{ day.isToday ? '今' : day.weekday }}
                </span>
              </div>
            </div>
            <!-- 「现在」顶部标签 -->
            <div
              v-if="model.nowPct !== null"
              class="pointer-events-none absolute bottom-0 top-0 z-20"
              :style="{ left: `${model.nowPct}%` }"
              aria-hidden="true"
            >
              <span class="absolute left-0 top-0 -translate-x-1/2 whitespace-nowrap rounded-b bg-hot px-1.5 py-0.5 text-[9px] font-bold leading-none text-white shadow-xs">
                现在
              </span>
              <span class="block h-full w-[2px] -translate-x-1/2 bg-hot/75" />
            </div>
          </div>
        </div>

        <!-- 空态 / 加载态 -->
        <div v-if="loading && !model.lanes.length" role="status" class="px-5 py-14 text-center text-sm text-muted">
          正在加载时间轴日程…
        </div>
        <div v-else-if="!model.lanes.length" class="px-5 py-14 text-center text-sm text-muted">
          当前时间窗口与筛选条件下暂无日程安排。
        </div>

        <!-- 按游戏分区泳道 -->
        <div v-else class="divide-y divide-line">
          <div
            v-for="lane in model.lanes"
            :key="lane.community"
            class="flex"
          >
            <!-- 左侧粘性游戏名与折叠控制 -->
            <div class="sticky left-0 z-30 flex w-[116px] shrink-0 flex-col justify-center gap-1 border-r border-line bg-card px-3 py-2.5 sm:w-[148px] sm:px-4">
              <div class="flex min-w-0 items-center gap-2">
                <span class="size-2 shrink-0 rounded-full" :class="laneMeta(lane.community).classes.dot" aria-hidden="true" />
                <span class="min-w-0 truncate text-[13px] font-semibold text-ink">{{ laneMeta(lane.community).label }}</span>
              </div>
              <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-4">
                <span class="text-[11px] tabular-nums text-muted">{{ lane.bars.length }} 项</span>
                <button
                  v-if="isLaneFoldable(lane)"
                  type="button"
                  class="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-semibold text-brand-ink transition-colors hover:bg-brand-soft"
                  :aria-expanded="isLaneExpanded(lane)"
                  @click="toggleLane(lane.community)"
                >
                  <template v-if="isLaneExpanded(lane)">
                    收起<ChevronUp :size="11" aria-hidden="true" />
                  </template>
                  <template v-else>
                    +{{ hiddenBarCount(lane) }}<ChevronDown :size="11" aria-hidden="true" />
                  </template>
                </button>
              </div>
            </div>

            <!-- 右侧甘特画布 -->
            <div class="relative flex-1 overflow-hidden">
              <div
                class="relative"
                :style="{ height: `${visibleTrackCount(lane) * TRACK_HEIGHT + LANE_PAD_Y * 2}px` }"
              >
                <!-- 背景日期网格列 -->
                <div
                  class="pointer-events-none absolute inset-0 grid"
                  :style="{ gridTemplateColumns: `repeat(${model.daysCount}, minmax(0, 1fr))` }"
                  aria-hidden="true"
                >
                  <div
                    v-for="day in model.axisDays"
                    :key="day.date"
                    class="border-r border-line-soft/65 last:border-r-0"
                    :class="[
                      day.isToday ? 'bg-brand/[0.045]' : day.isWeekend ? 'bg-chip/20' : '',
                      day.isMonthStart ? 'border-l border-l-line/80' : '',
                    ]"
                  />
                </div>

                <!-- 贯穿泳道的「现在」竖线 -->
                <div
                  v-if="model.nowPct !== null"
                  class="pointer-events-none absolute bottom-0 top-0 z-10 w-[2px] -translate-x-1/2 bg-hot/65"
                  :style="{ left: `${model.nowPct}%` }"
                  aria-hidden="true"
                />

                <!-- 日程条与里程碑点 -->
                <template v-for="bar in visibleBars(lane)" :key="bar.entry.id">
                  <!-- 区间日程条 -->
                  <button
                    v-if="!bar.isPoint"
                    type="button"
                    class="group absolute flex h-[30px] cursor-pointer items-center gap-1.5 overflow-hidden px-2 text-left text-xs transition-all focus-visible:z-25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                    :class="[
                      barClasses(bar.entry.community).chip,
                      bar.clippedLeft ? 'rounded-l-none' : 'rounded-l-lg',
                      bar.clippedRight ? 'rounded-r-none' : 'rounded-r-lg',
                      selectedId === bar.entry.id
                        ? 'z-20 ring-2 ring-brand shadow-sm'
                        : bar.endingSoon && !isCompleted(bar.entry)
                          ? 'z-[12] ring-1 ring-hot/80 hover:z-20'
                          : 'z-[11] ring-1 ring-inset ring-black/5 hover:z-20 hover:brightness-95 dark:ring-white/10 dark:hover:brightness-110',
                      isCompleted(bar.entry) ? 'opacity-50' : bar.status === 'ended' ? 'opacity-65' : '',
                    ]"
                    :style="{
                      left: `${bar.leftPct}%`,
                      width: `${bar.widthPct}%`,
                      top: `${bar.track * TRACK_HEIGHT + LANE_PAD_Y}px`,
                    }"
                    :title="barTooltip(bar)"
                    :aria-pressed="selectedId === bar.entry.id"
                    @click="emit('select', bar.entry)"
                  >
                    <!-- 左侧品牌色条 -->
                    <span
                      v-if="!bar.clippedLeft"
                      class="absolute bottom-0 left-0 top-0 w-[3px]"
                      :class="bar.endingSoon && !isCompleted(bar.entry) ? 'bg-hot' : barClasses(bar.entry.community).dot"
                      aria-hidden="true"
                    />
                    <!-- 底部进行中进度条 -->
                    <span
                      v-if="bar.progressPct !== null && bar.status === 'ongoing'"
                      class="pointer-events-none absolute bottom-0 left-0 h-[3px] opacity-75"
                      :class="bar.endingSoon && !isCompleted(bar.entry) ? 'bg-hot' : barClasses(bar.entry.community).dot"
                      :style="{ width: `${bar.progressPct}%` }"
                      aria-hidden="true"
                    />
                    <Check
                      v-if="isCompleted(bar.entry)"
                      :size="12"
                      class="shrink-0 text-ok"
                      aria-hidden="true"
                    />
                    <span
                      v-if="bar.widthPct >= 10"
                      class="shrink-0 rounded bg-card/65 px-1 py-0.5 text-[10px] font-semibold leading-none text-ink-2"
                    >
                      {{ KIND_LABELS[bar.entry.kind] ?? '活动' }}
                    </span>
                    <span
                      class="min-w-0 flex-1 truncate font-medium leading-none"
                      :class="{ 'line-through': isCompleted(bar.entry) }"
                    >
                      {{ displayTitle(bar.entry) }}
                    </span>
                    <span
                      v-if="bar.widthPct >= 18 && bar.badgeText && !isCompleted(bar.entry)"
                      class="shrink-0 whitespace-nowrap text-[10px] font-semibold tabular-nums leading-none"
                      :class="bar.endingSoon ? 'text-hot' : 'opacity-75'"
                    >
                      {{ bar.badgeText }}
                    </span>
                  </button>

                  <!-- 节点 / 直播 / 截止点事件 -->
                  <button
                    v-else
                    type="button"
                    class="group absolute z-[13] flex h-[30px] max-w-[220px] cursor-pointer items-center gap-1.5 rounded-full border bg-card/95 px-2 text-left text-[11px] shadow-2xs transition-all hover:z-20 focus-visible:z-25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                    :class="[
                      bar.alignRight ? '-translate-x-full flex-row-reverse' : '',
                      selectedId === bar.entry.id
                        ? 'z-20 border-brand ring-2 ring-brand/40'
                        : bar.endingSoon && !isCompleted(bar.entry)
                          ? 'border-hot/70 text-hot'
                          : 'border-line text-ink-2 hover:border-brand/50',
                      isCompleted(bar.entry) ? 'opacity-50' : '',
                    ]"
                    :style="{
                      left: `${bar.leftPct}%`,
                      top: `${bar.track * TRACK_HEIGHT + LANE_PAD_Y}px`,
                    }"
                    :title="barTooltip(bar)"
                    :aria-pressed="selectedId === bar.entry.id"
                    @click="emit('select', bar.entry)"
                  >
                    <span
                      class="size-2 shrink-0 rotate-45"
                      :class="isCompleted(bar.entry) ? 'bg-ok' : bar.endingSoon ? 'bg-hot' : barClasses(bar.entry.community).dot"
                      aria-hidden="true"
                    />
                    <span class="shrink-0 text-[10px] font-semibold" :class="bar.entry.kind === 'deadline' ? 'text-hot' : 'text-muted'">
                      {{ KIND_LABELS[bar.entry.kind] ?? '节点' }}
                    </span>
                    <span class="min-w-0 truncate font-medium text-ink" :class="{ 'line-through': isCompleted(bar.entry) }">
                      {{ displayTitle(bar.entry) }}
                    </span>
                  </button>
                </template>
              </div>

              <!-- 折叠泳道底部展开提示条 -->
              <button
                v-if="isLaneFoldable(lane) && !isLaneExpanded(lane)"
                type="button"
                class="flex w-full items-center justify-center gap-1 border-t border-line-soft bg-chip/30 py-1.5 text-[11px] font-medium text-muted transition-colors hover:bg-chip/65 hover:text-ink"
                @click="toggleLane(lane.community)"
              >
                展开 {{ laneMeta(lane.community).label }} 其余 {{ hiddenBarCount(lane) }} 项安排
                <ChevronDown :size="12" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
