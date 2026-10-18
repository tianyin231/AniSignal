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

export interface CalendarMilestone {
  entry: CalendarEntry
  date: string
  at: string
  phase: 'start' | 'end' | 'single' | 'deadline'
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
  // 月历只铺开始、结束和截止节点；持续活动仍保留在 items，供当天详情展开查看。
  const milestones = dated.flatMap((entry): CalendarMilestone[] => {
    const start = { entry, date: entry.startDay, at: entry.start_at! }
    if (entry.kind === 'deadline') return [{ ...start, phase: 'deadline' }]
    if (!entry.end_at) return [{ ...start, phase: 'start' }]
    const endDate = beijingDay(entry.end_at)
    if (endDate === entry.startDay) return [{ ...start, phase: 'single' }]
    return [{ ...start, phase: 'start' }, { entry, date: endDate, at: entry.end_at, phase: 'end' }]
  })
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(gridStart + index * DAY).toISOString().slice(0, 10)
    // 日期格先展示当天的开始 / 结束节点，长周期活动不会挤占整个月的预览。
    const dayOrder = (entry: typeof dated[number]) => entry.startDay === date ? 0 : entry.endDay === date ? 1 : 2
    return {
      date,
      number: Number(date.slice(-2)),
      inMonth: date.startsWith(month),
      items: dated.filter(entry => entry.startDay <= date && entry.endDay >= date).sort((a, b) => dayOrder(a) - dayOrder(b)),
      milestones: milestones.filter(point => point.date === date).sort((a, b) => timestamp(a.at) - timestamp(b.at) || a.entry.id - b.entry.id),
    }
  })
}
