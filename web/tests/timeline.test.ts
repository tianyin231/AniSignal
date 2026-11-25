import test from 'node:test'
import assert from 'node:assert/strict'
import {
  calendarDays,
  dayInZone,
  formatTimeInZone,
  zoneStartOfDayMs,
} from '../app/utils/calendar.ts'
import { buildTimeline } from '../app/utils/timeline.ts'
import {
  makeCodeKey,
  mergeCalendarStates,
  normalizeCalendarState,
} from '../app/utils/calendar-state.ts'
import type { CalendarEntry } from '../app/composables/usePublicApi'

function entry(id: number, start_at: string, end_at: string | null, extra: Partial<CalendarEntry> = {}): CalendarEntry {
  return {
    id,
    content_id: id,
    title: `日程 ${id}`,
    kind: 'activity',
    community: 'wuwa',
    start_at,
    end_at,
    all_day: false,
    delayed: false,
    url: null,
    ...extra,
  }
}

test('多时区日期与时间转换准确处理偏移与夏令时', () => {
  // 2026-09-30T15:30:00Z:
  // - 上海 UTC+8 -> 2026-09-30 23:30
  // - 东京 UTC+9 -> 2026-10-01 00:30
  // - 美东 EDT UTC-4 -> 2026-09-30 11:30
  const utc = '2026-09-30T15:30:00'
  assert.equal(dayInZone(utc, 'Asia/Shanghai'), '2026-09-30')
  assert.equal(dayInZone(utc, 'Asia/Tokyo'), '2026-10-01')
  assert.equal(dayInZone(utc, 'America/New_York'), '2026-09-30')
  assert.equal(formatTimeInZone(utc, 'Asia/Shanghai'), '09-30 23:30')
  assert.equal(formatTimeInZone(utc, 'Asia/Tokyo'), '10-01 00:30')
  assert.equal(formatTimeInZone(utc, 'America/New_York'), '09-30 11:30')

  // 夏令时与冬令时零点校验
  assert.equal(new Date(zoneStartOfDayMs('2026-10-01', 'Asia/Shanghai')).toISOString(), '2026-09-30T16:00:00.000Z')
  assert.equal(new Date(zoneStartOfDayMs('2026-07-01', 'America/New_York')).toISOString(), '2026-07-01T04:00:00.000Z')
  assert.equal(new Date(zoneStartOfDayMs('2026-12-01', 'America/New_York')).toISOString(), '2026-12-01T05:00:00.000Z')

  // 月历按不同时区落到不同格
  const tokyoOct = calendarDays('2026-10', [entry(1, utc, null)], 'Asia/Tokyo')
  assert.equal(tokyoOct.find(d => d.date === '2026-10-01')!.milestones.length, 1)
  const shanghaiOct = calendarDays('2026-10', [entry(1, utc, null)], 'Asia/Shanghai')
  assert.equal(shanghaiOct.find(d => d.date === '2026-10-01')!.milestones.length, 0)
  assert.equal(shanghaiOct.find(d => d.date === '2026-09-30')!.milestones.length, 1)
})

test('甘特时间轴：跨月刻度、现在竖线、非重叠轨道排布与边界裁切', () => {
  // 窗口：2026-10-29 起 14 天（跨 10/11 月），现在为北京时间 2026-11-01 12:00
  const now = Date.parse('2026-11-01T04:00:00Z')
  const model = buildTimeline({
    startDay: '2026-10-29',
    daysCount: 14,
    now,
    timeZone: 'Asia/Shanghai',
    communityOrder: ['hsr', 'wuwa'],
    entries: [
      // 1: 窗口前开始、窗口中结束（左裁切，剩 2 天 -> 临期）
      entry(1, '2026-10-20T02:00:00', '2026-11-03T04:00:00', { community: 'wuwa', kind: 'pool' }),
      // 2: 与 1 重叠 -> 应落入轨道 1
      entry(2, '2026-10-30T02:00:00', '2026-11-20T04:00:00', { community: 'wuwa', kind: 'activity' }),
      // 3: 紧接在 1 结束之后开始 -> 应复用轨道 0
      entry(3, '2026-11-05T02:00:00', '2026-11-09T02:00:00', { community: 'wuwa', kind: 'activity' }),
      // 4: 崩铁前瞻点事件（无结束时间，靠右边缘 -> alignRight=true）
      entry(4, '2026-11-10T11:30:00', null, { community: 'hsr', kind: 'broadcast' }),
      // 5: 窗口外活动 -> 不出现在模型中
      entry(5, '2026-12-01T02:00:00', '2026-12-10T02:00:00', { community: 'genshin' }),
    ],
  })

  assert.equal(model.startDay, '2026-10-29')
  assert.equal(model.endDay, '2026-11-11')
  assert.equal(model.axisDays.length, 14)
  assert.equal(model.axisDays[0]!.isMonthStart, true)
  assert.equal(model.axisDays.find(d => d.date === '2026-11-01')!.isMonthStart, true)
  assert.equal(model.axisDays.find(d => d.date === '2026-11-01')!.isToday, true)
  assert.ok(model.nowPct !== null && model.nowPct > 20 && model.nowPct < 30)

  // 按 communityOrder 排序，且排除窗口外无日程分区
  assert.deepEqual(model.lanes.map(l => l.community), ['hsr', 'wuwa'])
  assert.equal(model.totalBars, 4)

  const wuwaLane = model.lanes[1]!
  assert.equal(wuwaLane.trackCount, 2)
  const bar1 = wuwaLane.bars.find(b => b.entry.id === 1)!
  const bar2 = wuwaLane.bars.find(b => b.entry.id === 2)!
  const bar3 = wuwaLane.bars.find(b => b.entry.id === 3)!
  assert.equal(bar1.clippedLeft, true)
  assert.equal(bar1.clippedRight, false)
  assert.equal(bar1.endingSoon, true)
  assert.equal(bar1.status, 'ongoing')
  assert.equal(bar2.clippedRight, true)
  assert.notEqual(bar1.track, bar2.track)
  // bar3 在 bar1 结束后才开始，应复用轨道 0
  assert.equal(bar3.track, bar1.track)

  const hsrBar = model.lanes[0]!.bars[0]!
  assert.equal(hsrBar.isPoint, true)
  assert.equal(hsrBar.alignRight, true)
  assert.equal(hsrBar.status, 'upcoming')
})

test('个人打卡状态规范化与登录合并保留本地数据', () => {
  assert.equal(makeCodeKey(' wuwa2026 ', 'WuWa'), 'wuwa:WUWA2026')
  const normalized = normalizeCalendarState({
    claimed_codes: ['wuwa:A', 'wuwa:A', '', 123],
    completed_schedules: [5, -1, 5, 1.5, 9],
    timezone: 'Invalid/Zone',
  })
  assert.deepEqual(normalized, {
    claimed_codes: ['wuwa:A'],
    completed_schedules: [5, 9],
    timezone: 'Asia/Shanghai',
  })

  const { merged, dirty } = mergeCalendarStates(
    { claimed_codes: ['wuwa:LOCAL'], completed_schedules: [10], timezone: 'Asia/Tokyo' },
    { claimed_codes: ['hsr:REMOTE'], completed_schedules: [20], timezone: 'Asia/Shanghai' },
  )
  assert.equal(dirty, true)
  assert.deepEqual(merged, {
    claimed_codes: ['hsr:REMOTE', 'wuwa:LOCAL'],
    completed_schedules: [20, 10],
    timezone: 'Asia/Tokyo',
  })
})
