/**
 * 活动日历横向甘特时间轴的纯布局计算：
 * - 按指定时区生成日期刻度与「现在」竖线百分比；
 * - 按游戏分区归组泳道，并在每条泳道内贪心排布非重叠轨道（区间条 + 里程碑点事件）；
 * - 区分进行中/即将开始/已结束与临期预警（<4 天）。
 */

import type { CalendarEntry } from '../composables/usePublicApi'
import {
  DEFAULT_CALENDAR_TIMEZONE,
  dayInZone,
  shiftIsoDay,
  timestamp,
  zoneStartOfDayMs,
  type CalendarTimezoneId,
} from './calendar.ts'
import { ENDING_SOON_MS, fmtRemaining } from './schedule.ts'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

export interface TimelineAxisDay {
  date: string
  dayNum: number
  month: number
  weekday: string
  isWeekend: boolean
  isToday: boolean
  isMonthStart: boolean
  monthLabel: string
}

export interface TimelineBar {
  entry: CalendarEntry
  leftPct: number
  widthPct: number
  clippedLeft: boolean
  clippedRight: boolean
  isPoint: boolean
  /** 当点事件靠近右边界时，把文字标签放在节点左侧避免裁切。 */
  alignRight: boolean
  status: 'ongoing' | 'upcoming' | 'ended'
  endingSoon: boolean
  badgeText: string
  progressPct: number | null
  track: number
}

export interface TimelineLane {
  community: string
  bars: TimelineBar[]
  trackCount: number
}

export interface TimelineModel {
  startDay: string
  endDay: string
  daysCount: number
  axisDays: TimelineAxisDay[]
  nowPct: number | null
  lanes: TimelineLane[]
  totalBars: number
}

export interface BuildTimelineOptions {
  startDay: string
  daysCount: number
  entries: CalendarEntry[]
  now: number
  timeZone?: CalendarTimezoneId | string
  communityOrder?: string[]
}

export function buildTimeline(options: BuildTimelineOptions): TimelineModel {
  const {
    startDay,
    daysCount,
    entries,
    now,
    timeZone = DEFAULT_CALENDAR_TIMEZONE,
    communityOrder = [],
  } = options

  const safeDays = Math.max(7, Math.min(90, Math.round(daysCount)))
  const endDay = shiftIsoDay(startDay, safeDays - 1)
  const todayStr = dayInZone(now, timeZone)

  const windowStartMs = zoneStartOfDayMs(startDay, timeZone)
  const windowEndMs = zoneStartOfDayMs(shiftIsoDay(startDay, safeDays), timeZone)
  const windowSpanMs = Math.max(1, windowEndMs - windowStartMs)

  const axisDays: TimelineAxisDay[] = Array.from({ length: safeDays }, (_, idx) => {
    const date = shiftIsoDay(startDay, idx)
    const [y = 1970, m = 1, d = 1] = date.split('-').map(Number)
    const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
    return {
      date,
      dayNum: d,
      month: m,
      weekday: WEEKDAYS[dow] ?? '',
      isWeekend: dow === 0 || dow === 6,
      isToday: date === todayStr,
      isMonthStart: idx === 0 || d === 1,
      monthLabel: `${m}月`,
    }
  })

  const nowPct = now >= windowStartMs && now <= windowEndMs
    ? Math.max(0, Math.min(100, ((now - windowStartMs) / windowSpanMs) * 100))
    : null

  // 点事件（直播/截止/未知结束）的文字标签预留视觉宽度，用于同泳道防重叠排布
  const pointFootprintPct = Math.min(28, Math.max(10, (100 / safeDays) * 3.2))
  const minBarWidthPct = Math.max(1.8, (100 / safeDays) * 0.45)

  const grouped = new Map<string, Omit<TimelineBar, 'track'>[]>()

  for (const entry of entries) {
    if (!entry.start_at) continue
    const rawStart = timestamp(entry.start_at)
    if (!Number.isFinite(rawStart)) continue

    const hasEnd = Boolean(entry.end_at) && entry.kind !== 'deadline'
    const rawEnd = hasEnd ? timestamp(entry.end_at!) : Number.NaN
    if (hasEnd && (!Number.isFinite(rawEnd) || rawEnd < rawStart)) continue

    const isPoint = !hasEnd || rawEnd === rawStart

    let startMs = rawStart
    let endMs = rawEnd
    if (!isPoint && entry.all_day) {
      startMs = zoneStartOfDayMs(dayInZone(rawStart, timeZone), timeZone)
      endMs = zoneStartOfDayMs(shiftIsoDay(dayInZone(rawEnd, timeZone), 1), timeZone)
    }

    if (isPoint) {
      if (startMs < windowStartMs || startMs > windowEndMs) continue
    } else {
      if (endMs <= windowStartMs || startMs >= windowEndMs) continue
    }

    let leftPct: number
    let widthPct: number
    let clippedLeft = false
    let clippedRight = false
    let status: TimelineBar['status']
    let endingSoon = false
    let badgeText = ''
    let progressPct: number | null = null

    if (isPoint) {
      leftPct = Math.max(0, Math.min(100, ((startMs - windowStartMs) / windowSpanMs) * 100))
      widthPct = 0
      if (now < startMs) {
        status = 'upcoming'
        const rem = startMs - now
        endingSoon = entry.kind === 'deadline' && rem < ENDING_SOON_MS
        badgeText = entry.kind === 'deadline' ? `${fmtRemaining(rem)}后截止` : `${fmtRemaining(rem)}后`
      } else {
        status = 'ended'
        badgeText = entry.kind === 'deadline' ? '已截止' : '已开启'
      }
    } else {
      const clampedStart = Math.max(startMs, windowStartMs)
      const clampedEnd = Math.min(endMs, windowEndMs)
      clippedLeft = startMs < windowStartMs
      clippedRight = endMs > windowEndMs
      leftPct = ((clampedStart - windowStartMs) / windowSpanMs) * 100
      const naturalWidth = ((clampedEnd - clampedStart) / windowSpanMs) * 100
      widthPct = Math.min(100 - leftPct, Math.max(naturalWidth, minBarWidthPct))

      if (now < startMs) {
        status = 'upcoming'
        badgeText = `${fmtRemaining(startMs - now)}后`
      } else if (now >= endMs) {
        status = 'ended'
        badgeText = '已结束'
        progressPct = 100
      } else {
        status = 'ongoing'
        const rem = endMs - now
        endingSoon = rem < ENDING_SOON_MS
        badgeText = `剩 ${fmtRemaining(rem)}`
        progressPct = Math.min(100, Math.max(0, Math.round(((now - startMs) / Math.max(1, endMs - startMs)) * 100)))
      }
    }

    const alignRight = isPoint && leftPct > 76
    const community = entry.community || 'other'
    let list = grouped.get(community)
    if (!list) {
      list = []
      grouped.set(community, list)
    }
    list.push({
      entry,
      leftPct,
      widthPct,
      clippedLeft,
      clippedRight,
      isPoint,
      alignRight,
      status,
      endingSoon,
      badgeText,
      progressPct,
    })
  }

  const orderIndex = new Map(communityOrder.map((key, idx) => [key, idx]))
  const sortedCommunities = Array.from(grouped.keys()).sort((a, b) => {
    const ia = orderIndex.has(a) ? orderIndex.get(a)! : Number.MAX_SAFE_INTEGER
    const ib = orderIndex.has(b) ? orderIndex.get(b)! : Number.MAX_SAFE_INTEGER
    if (ia !== ib) return ia - ib
    return a.localeCompare(b)
  })

  const lanes: TimelineLane[] = []
  let totalBars = 0

  for (const community of sortedCommunities) {
    const rawBars = grouped.get(community)!
    // 同泳道内：先排起点早的，同起点先排区间更长的（视觉上类似甘特阶梯，整洁紧凑）
    rawBars.sort((a, b) =>
      a.leftPct - b.leftPct
      || Number(a.isPoint) - Number(b.isPoint)
      || b.widthPct - a.widthPct
      || a.entry.id - b.entry.id,
    )

    const trackEnds: number[] = []
    const placed: TimelineBar[] = []
    const GAP_PCT = 0.7

    for (const bar of rawBars) {
      const spanStart = bar.isPoint && bar.alignRight
        ? Math.max(0, bar.leftPct - pointFootprintPct)
        : bar.leftPct
      const spanEnd = bar.isPoint
        ? (bar.alignRight ? bar.leftPct : Math.min(100, bar.leftPct + pointFootprintPct))
        : Math.min(100, bar.leftPct + bar.widthPct)

      let track = -1
      for (let t = 0; t < trackEnds.length; t++) {
        if (trackEnds[t]! + GAP_PCT <= spanStart) {
          track = t
          trackEnds[t] = spanEnd
          break
        }
      }
      if (track === -1) {
        track = trackEnds.length
        trackEnds.push(spanEnd)
      }
      placed.push({ ...bar, track })
    }

    lanes.push({
      community,
      bars: placed,
      trackCount: Math.max(1, trackEnds.length),
    })
    totalBars += placed.length
  }

  return {
    startDay,
    endDay,
    daysCount: safeDays,
    axisDays,
    nowPct,
    lanes,
    totalBars,
  }
}
