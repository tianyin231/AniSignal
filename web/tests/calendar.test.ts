import test from 'node:test'
import assert from 'node:assert/strict'
import { beijingDay, calendarDays, shiftCalendarMonth } from '../app/utils/calendar.ts'
import type { CalendarEntry } from '../app/composables/usePublicApi'

function entry(id: number, start_at: string, end_at: string | null, extra = {}): CalendarEntry {
  return { id, content_id: id, title: `活动 ${id}`, kind: 'activity', start_at, end_at, all_day: false, delayed: false, url: null, ...extra }
}

test('月历从周一开始，包含闰日，跨年切月正确', () => {
  const days = calendarDays('2024-02', [])
  assert.equal(days.length, 42)
  assert.equal(days[0]!.date, '2024-01-29')
  assert.equal(days[41]!.date, '2024-03-10')
  assert.equal(days.filter(day => day.inMonth).length, 29)
  assert.equal(shiftCalendarMonth('2026-12', 1), '2027-01')
  assert.equal(shiftCalendarMonth('2026-01', -1), '2025-12')
})

test('按北京时间定位跨月活动，未知结束时间不铺满月历', () => {
  assert.equal(beijingDay('2026-09-30T16:00:00'), '2026-10-01')
  const days = calendarDays('2026-10', [
    entry(1, '2026-09-30T15:00:00', '2026-10-02T10:00:00'),
    entry(2, '2026-09-30T16:00:00', null),
    entry(3, '2026-09-30T16:00:00', '2026-10-01T16:00:00'),
    entry(4, '2026-09-30T16:00:00', '2026-10-01T16:00:00', { all_day: true }),
    entry(5, '2026-09-30T16:00:00', '2026-10-03T16:00:00', { kind: 'deadline' }),
  ])
  const ids = (date: string) => days.find(day => day.date === date)!.items.map(item => item.id).sort()
  assert.deepEqual(ids('2026-09-30'), [1])
  assert.deepEqual(ids('2026-10-01'), [1, 2, 3, 4, 5])
  assert.deepEqual(ids('2026-10-02'), [1, 4])
  assert.deepEqual(ids('2026-10-03'), [])
})

test('当天开始和结束的安排先于贯穿整月的活动显示', () => {
  const day = calendarDays('2026-09', [
    entry(1, '2026-08-20T02:00:00', '2026-10-01T10:00:00'),
    entry(2, '2026-09-01T02:00:00', '2026-09-21T10:00:00'),
    entry(3, '2026-09-21T02:00:00', null),
  ]).find(day => day.date === '2026-09-21')!
  assert.deepEqual(day.items.map(item => item.id), [3, 2, 1])
})

test('跨周活动只在起止日显示节点，期间仍能查到完整活动', () => {
  const days = calendarDays('2026-10', [entry(1, '2026-10-02T02:00:00', '2026-10-22T03:59:00')])
  const points = days.flatMap(day => day.milestones)
  assert.deepEqual(points.map(point => [point.date, point.phase]), [['2026-10-02', 'start'], ['2026-10-22', 'end']])
  const middle = days.find(day => day.date === '2026-10-18')!
  assert.equal(middle.milestones.length, 0)
  assert.deepEqual(middle.items.map(item => item.id), [1])
})

test('零点结束的节点标在实际截止日，进行中列表不延长一天', () => {
  const day = calendarDays('2026-10', [entry(1, '2026-09-30T16:00:00', '2026-10-01T16:00:00')])
    .find(day => day.date === '2026-10-02')!
  assert.equal(day.items.length, 0)
  assert.deepEqual(day.milestones.map(point => [point.entry.id, point.phase, point.at]), [[1, 'end', '2026-10-01T16:00:00']])
})

test('截止事项和单日活动只显示一次，同日节点按实际时间排序', () => {
  const day = calendarDays('2026-10', [
    entry(1, '2026-10-22T02:00:00', '2026-10-22T04:00:00'),
    entry(2, '2026-10-21T20:00:00', null),
    entry(3, '2026-10-21T19:59:00', '2026-10-24T10:00:00', { kind: 'deadline' }),
  ]).find(day => day.date === '2026-10-22')!
  assert.deepEqual(day.milestones.map(point => [point.entry.id, point.phase]), [[3, 'deadline'], [2, 'start'], [1, 'single']])
})
