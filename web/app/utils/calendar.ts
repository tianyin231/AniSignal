import type { CalendarEntry } from '../composables/usePublicApi'

const DAY = 24 * 60 * 60 * 1000
const BEIJING_OFFSET = 8 * 60 * 60 * 1000

function timestamp(value: string) {
  return Date.parse(/Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`)
}

export function beijingDay(value: string | number) {
  return new Date((typeof value === 'number' ? value : timestamp(value)) + BEIJING_OFFSET).toISOString().slice(0, 10)
}

export function shiftCalendarMonth(month: string, offset: number) {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year!, number! - 1 + offset, 1)).toISOString().slice(0, 7)
}

export function calendarDays(month: string, entries: CalendarEntry[]) {
  const first = new Date(`${month}-01T00:00:00Z`)
  const gridStart = first.getTime() - ((first.getUTCDay() + 6) % 7) * DAY
  const dated = entries.filter(entry => entry.start_at).map(entry => {
    const start = timestamp(entry.start_at!)
    // 未公布结束日期只标开始当天；截止事项也只标截止日。
    const end = entry.end_at && entry.kind !== 'deadline'
      ? Math.max(start, timestamp(entry.end_at) - (entry.all_day ? 0 : 1))
      : start
    return { ...entry, startDay: beijingDay(start), endDay: beijingDay(end) }
  })
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(gridStart + index * DAY).toISOString().slice(0, 10)
    return {
      date,
      number: Number(date.slice(-2)),
      inMonth: date.startsWith(month),
      items: dated.filter(entry => entry.startDay <= date && entry.endDay >= date),
    }
  })
}
