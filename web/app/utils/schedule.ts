/**
 * 首页日程速览的纯计算：进行中活动的进度 / 剩余时间，与"即将到来"列表。
 * 时间一律按北京时间展示；后端时间串是 UTC（无时区后缀按 UTC 解析）。
 */

import type { CalendarDto, CalendarEntry, RedeemCodeDto } from '../composables/usePublicApi'

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const BEIJING_OFFSET = 8 * HOUR
const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

/** 剩余不足这个时长的进行中活动标为"即将结束"。 */
export const ENDING_SOON_MS = 4 * DAY

export const KIND_LABELS: Record<string, string> = {
  activity: '活动',
  maintenance: '维护',
  pool: '卡池',
  broadcast: '直播',
  delay: '变更',
  deadline: '截止',
}

export function toTimestamp(value: string | null | undefined): number {
  if (!value) return Number.NaN
  return Date.parse(/Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** 北京时间的日期部件：月 / 日 / 星期 / 时分。 */
export function beijingParts(ts: number) {
  const d = new Date(ts + BEIJING_OFFSET)
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
    weekday: `周${WEEKDAYS[d.getUTCDay()]}`,
    hm: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`,
  }
}

/** 北京时间 MM/DD HH:mm */
export function fmtShort(ts: number): string {
  const p = beijingParts(ts)
  return `${pad(p.month)}/${pad(p.day)} ${p.hm}`
}

/** 剩余时长：3天9时 / 5时20分 / 12分 / 已结束 */
export function fmtRemaining(ms: number): string {
  if (ms <= 0) return '已结束'
  const days = Math.floor(ms / DAY)
  const hours = Math.floor((ms % DAY) / HOUR)
  const minutes = Math.floor((ms % HOUR) / MINUTE)
  if (days > 0) return hours > 0 ? `${days}天${hours}时` : `${days}天`
  if (hours > 0) return minutes > 0 ? `${hours}时${minutes}分` : `${hours}时`
  return `${Math.max(1, minutes)}分`
}

/** 按北京日历日计算的相对日期：今天 / 明天 / N 天后 */
export function relativeDay(ts: number, now: number): string {
  const dayIndex = (t: number) => Math.floor((t + BEIJING_OFFSET) / DAY)
  const diff = dayIndex(ts) - dayIndex(now)
  if (diff <= 0) return '今天'
  if (diff === 1) return '明天'
  return `${diff} 天后`
}

export interface ActivityProgress {
  entry: CalendarEntry
  /** 已进行的比例，0–100 */
  percent: number
  remainingMs: number
  endTs: number
  endingSoon: boolean
}

/** 进行中且起止时间都已知的活动，按结束时间升序（最先结束的排前）。 */
export function ongoingActivities(entries: CalendarEntry[], now: number): ActivityProgress[] {
  return entries
    .map((entry) => {
      const start = toTimestamp(entry.start_at)
      const end = toTimestamp(entry.end_at)
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || now < start || now >= end) return null
      const remainingMs = end - now
      return {
        entry,
        percent: Math.min(100, Math.max(0, Math.round(((now - start) / (end - start)) * 100))),
        remainingMs,
        endTs: end,
        endingSoon: remainingMs < ENDING_SOON_MS,
      }
    })
    .filter((item): item is ActivityProgress => item !== null)
    .sort((a, b) => a.endTs - b.endTs)
}

export interface UpNextItem {
  key: string
  title: string
  at: number
  /** 类型文案：截止 / 开始 / 兑换码到期 … */
  kind: string
  allDay: boolean
  community?: string
  contentId?: number
  url?: string | null
  code?: string
}

/** "即将到来"：未开始的日程（含截止事项）与未过期的兑换码，按发生时间升序。 */
export function upNextItems(
  calendar: CalendarDto | null | undefined,
  codes: RedeemCodeDto[] | null | undefined,
  now: number,
  limit = 5,
): UpNextItem[] {
  const items: UpNextItem[] = []
  for (const entry of calendar?.upcoming ?? []) {
    const at = toTimestamp(entry.start_at)
    if (!Number.isFinite(at) || at <= now) continue
    items.push({
      key: `schedule-${entry.id}`,
      title: entry.title,
      at,
      kind: entry.kind === 'deadline' ? '截止' : `${KIND_LABELS[entry.kind] ?? '日程'}开始`,
      allDay: entry.all_day,
      community: entry.community,
      contentId: entry.content_id,
      url: entry.url,
    })
  }
  for (const code of codes ?? []) {
    const at = toTimestamp(code.expires_at)
    if (!Number.isFinite(at) || at <= now) continue
    items.push({
      key: `code-${code.community ?? ''}-${code.code}`,
      title: `兑换码 ${code.code}`,
      at,
      kind: '兑换码到期',
      allDay: false,
      community: code.community,
      url: code.url,
      code: code.code,
    })
  }
  return items.sort((a, b) => a.at - b.at).slice(0, limit)
}
