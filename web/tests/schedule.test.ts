import test from 'node:test'
import assert from 'node:assert/strict'
import { beijingParts, fmtRemaining, ongoingActivities, relativeDay, toTimestamp, upNextItems } from '../app/utils/schedule.ts'
import type { CalendarEntry } from '../app/composables/usePublicApi'

function entry(id: number, start_at: string | null, end_at: string | null, extra: Partial<CalendarEntry> = {}): CalendarEntry {
  return { id, content_id: id, title: `活动 ${id}`, kind: 'activity', start_at, end_at, all_day: false, delayed: false, url: null, ...extra }
}

// 2026-10-18 18:20 北京时间
const NOW = toTimestamp('2026-10-18T10:20:00')

test('进行中活动按结束时间排序，进度与剩余时间按北京时间计算', () => {
  const list = ongoingActivities([
    entry(1, '2026-10-15T02:00:00', '2026-11-01T01:59:00'),
    entry(2, '2026-10-07T20:00:00', '2026-10-21T19:59:00', { kind: 'pool' }),
    entry(3, '2026-10-19T00:00:00', '2026-10-25T00:00:00'), // 尚未开始
    entry(4, '2026-10-01T00:00:00', null), // 结束时间未知
  ], NOW)
  assert.deepEqual(list.map(item => item.entry.id), [2, 1])
  assert.equal(list[0]!.percent, 76)
  assert.equal(fmtRemaining(list[0]!.remainingMs), '3天9时')
  assert.equal(list[0]!.endingSoon, true)
  assert.equal(list[1]!.percent, 20)
  assert.equal(list[1]!.endingSoon, false)
})

test('剩余时长文案', () => {
  assert.equal(fmtRemaining(0), '已结束')
  assert.equal(fmtRemaining(30_000), '1分')
  assert.equal(fmtRemaining(5 * 3_600_000 + 20 * 60_000), '5时20分')
  assert.equal(fmtRemaining(2 * 86_400_000), '2天')
})

test('北京日历日的相对日期与星期', () => {
  assert.equal(relativeDay(toTimestamp('2026-10-18T15:59:00'), NOW), '今天')
  assert.equal(relativeDay(toTimestamp('2026-10-18T16:00:00'), NOW), '明天')
  assert.equal(relativeDay(toTimestamp('2026-10-20T15:59:59'), NOW), '2 天后')
  const parts = beijingParts(toTimestamp('2026-10-20T15:59:59'))
  assert.deepEqual([parts.month, parts.day, parts.weekday, parts.hm], [10, 20, '周二', '23:59'])
})

test('即将到来合并日程与兑换码，跳过已过去的项目并按时间截断', () => {
  const items = upNextItems(
    {
      ongoing: [],
      recent: [],
      ended: [],
      upcoming: [
        entry(10, '2026-10-23T12:00:00', null, { kind: 'deadline', title: '互动抽奖截止' }),
        entry(11, '2026-10-21T20:00:00', null, { title: '标准寻访更新' }),
        entry(12, '2026-10-17T00:00:00', null, { title: '已经开始' }),
      ],
    },
    [{ code: 'WUWA2026', expires_at: '2026-10-19T15:59:00', created_at: '2026-10-15T00:00:00', url: null }],
    NOW,
    2,
  )
  assert.deepEqual(items.map(item => item.title), ['兑换码 WUWA2026', '标准寻访更新'])
  assert.equal(items[1]!.kind, '活动开始')
})
