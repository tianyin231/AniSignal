import type { CalendarEntry } from '../composables/usePublicApi'

const DAY = 24 * 60 * 60 * 1000
const BEIJING_OFFSET = 8 * 60 * 60 * 1000

export type CalendarTimezoneId =
  | 'Asia/Shanghai'
  | 'Asia/Tokyo'
  | 'America/New_York'
  | 'Europe/Berlin'
  | 'UTC'
  | 'local'

export interface CalendarTimezoneOption {
  id: CalendarTimezoneId
  label: string
  shortLabel: string
  hint: string
}

export const DEFAULT_CALENDAR_TIMEZONE: CalendarTimezoneId = 'Asia/Shanghai'

export const CALENDAR_TIMEZONES: CalendarTimezoneOption[] = [
  { id: 'Asia/Shanghai', label: '北京时间 · 国服 / 亚服', shortLabel: '北京 UTC+8', hint: 'UTC+8' },
  { id: 'Asia/Tokyo', label: '东京时间 · 日服', shortLabel: '东京 UTC+9', hint: 'UTC+9' },
  { id: 'America/New_York', label: '美东时间 · 美服', shortLabel: '美东', hint: 'UTC-5/-4' },
  { id: 'Europe/Berlin', label: '中欧时间 · 欧服', shortLabel: '中欧', hint: 'UTC+1/+2' },
  { id: 'UTC', label: 'UTC 标准时间', shortLabel: 'UTC', hint: 'UTC+0' },
  { id: 'local', label: '浏览器本地时区', shortLabel: '本地时区', hint: '自动' },
]

const VALID_TIMEZONE_IDS = new Set<string>(CALENDAR_TIMEZONES.map(item => item.id))

export function isValidTimezoneId(value: unknown): value is CalendarTimezoneId {
  return typeof value === 'string' && VALID_TIMEZONE_IDS.has(value)
}

export function resolveIanaTimeZone(pref: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE): string {
  if (pref === 'local') {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_CALENDAR_TIMEZONE
    } catch {
      return DEFAULT_CALENDAR_TIMEZONE
    }
  }
  return pref || DEFAULT_CALENDAR_TIMEZONE
}

export function timezoneMeta(pref: CalendarTimezoneId | string): CalendarTimezoneOption {
  return CALENDAR_TIMEZONES.find(item => item.id === pref) ?? CALENDAR_TIMEZONES[0]!
}

const formatterCache = new Map<string, Intl.DateTimeFormat>()

function getPartsFormatter(ianaZone: string): Intl.DateTimeFormat {
  let fmt = formatterCache.get(ianaZone)
  if (!fmt) {
    try {
      fmt = new Intl.DateTimeFormat('en-CA', {
        timeZone: ianaZone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      })
    } catch {
      fmt = new Intl.DateTimeFormat('en-CA', {
        timeZone: DEFAULT_CALENDAR_TIMEZONE,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      })
    }
    formatterCache.set(ianaZone, fmt)
  }
  return fmt
}

export function timestamp(value: string): number {
  return Date.parse(/Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`)
}

export interface ZonedDateParts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  date: string
  md: string
  hm: string
  full: string
}

export function partsInZone(
  value: string | number,
  timeZone: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE,
): ZonedDateParts {
  const ms = typeof value === 'number' ? value : timestamp(value)
  const iana = resolveIanaTimeZone(timeZone)
  const parts = getPartsFormatter(iana).formatToParts(new Date(ms))
  let year = '1970'
  let month = '01'
  let day = '01'
  let hour = '00'
  let minute = '00'
  for (const part of parts) {
    if (part.type === 'year') year = part.value
    else if (part.type === 'month') month = part.value
    else if (part.type === 'day') day = part.value
    else if (part.type === 'hour') hour = part.value === '24' ? '00' : part.value
    else if (part.type === 'minute') minute = part.value
  }
  return {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
    date: `${year}-${month}-${day}`,
    md: `${month}-${day}`,
    hm: `${hour}:${minute}`,
    full: `${year}-${month}-${day} ${hour}:${minute}`,
  }
}

export function dayInZone(
  value: string | number,
  timeZone: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE,
): string {
  return partsInZone(value, timeZone).date
}

export function formatTimeInZone(
  value: string | number | null | undefined,
  timeZone: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE,
  options: { dateOnly?: boolean; withYear?: boolean; timeOnly?: boolean } = {},
): string {
  if (value === null || value === undefined || value === '') return '—'
  const ms = typeof value === 'number' ? value : timestamp(value)
  if (!Number.isFinite(ms)) return '—'
  const p = partsInZone(ms, timeZone)
  if (options.timeOnly) return p.hm
  if (options.dateOnly) return options.withYear ? p.date : p.md
  return options.withYear ? p.full : `${p.md} ${p.hm}`
}

/** 计算某个 IANA 时区下 YYYY-MM-DD 00:00:00 对应的 UTC 毫秒时间戳（准确处理夏令时）。 */
export function zoneStartOfDayMs(
  isoDay: string,
  timeZone: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE,
): number {
  const [y = 1970, m = 1, d = 1] = isoDay.split('-').map(Number)
  const targetUtc = Date.UTC(y, m - 1, d, 0, 0, 0)
  const iana = resolveIanaTimeZone(timeZone)
  let guess = targetUtc
  for (let i = 0; i < 2; i++) {
    const p = partsInZone(guess, iana)
    const rendered = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, 0)
    const diff = rendered - targetUtc
    if (diff === 0) break
    guess -= diff
  }
  return guess
}

export function shiftIsoDay(isoDay: string, deltaDays: number): string {
  const [y = 1970, m = 1, d = 1] = isoDay.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + deltaDays)).toISOString().slice(0, 10)
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

export function calendarDays(
  month: string,
  entries: CalendarEntry[],
  timeZone: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE,
) {
  const first = new Date(`${month}-01T00:00:00Z`)
  const gridStart = first.getTime() - ((first.getUTCDay() + 6) % 7) * DAY
  const dated = entries.filter(entry => entry.start_at).map(entry => {
    const start = timestamp(entry.start_at!)
    // 未公布结束日期只标开始当天；截止事项也只标截止日。
    const end = entry.end_at && entry.kind !== 'deadline'
      ? Math.max(start, timestamp(entry.end_at) - (entry.all_day ? 0 : 1))
      : start
    return { ...entry, startDay: dayInZone(start, timeZone), endDay: dayInZone(end, timeZone) }
  })
  // 月历只铺开始、结束和截止节点；持续活动仍保留在 items，供当天详情展开查看。
  const milestones = dated.flatMap((entry): CalendarMilestone[] => {
    const start = { entry, date: entry.startDay, at: entry.start_at! }
    if (entry.kind === 'deadline') return [{ ...start, phase: 'deadline' }]
    if (!entry.end_at) return [{ ...start, phase: 'start' }]
    const endDate = dayInZone(entry.end_at, timeZone)
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
