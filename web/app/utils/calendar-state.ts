/**
 * 活动日历个人打卡与时区偏好的纯数据工具（便于前后端共用约定与 Node 单测）。
 */

import {
  DEFAULT_CALENDAR_TIMEZONE,
  formatTimeInZone,
  isValidTimezoneId,
  timestamp,
  type CalendarTimezoneId,
} from './calendar.ts'

export const MAX_CLAIMED_CODES = 200
export const MAX_COMPLETED_SCHEDULES = 300

export interface CalendarStatePayload {
  claimed_codes: string[]
  completed_schedules: number[]
  timezone: CalendarTimezoneId
}

export function makeCodeKey(code: string, community?: string | null): string {
  const cleanCode = code.trim().toUpperCase()
  const cleanCommunity = (community || 'all').trim().toLowerCase()
  return `${cleanCommunity}:${cleanCode}`
}

export function normalizeCalendarState(raw: unknown): CalendarStatePayload {
  if (!raw || typeof raw !== 'object') {
    return { claimed_codes: [], completed_schedules: [], timezone: DEFAULT_CALENDAR_TIMEZONE }
  }
  const obj = raw as Record<string, unknown>
  const claimedSet = new Set<string>()
  if (Array.isArray(obj.claimed_codes)) {
    for (const item of obj.claimed_codes) {
      if (typeof item === 'string' && item.trim() && item.trim().length <= 80) {
        claimedSet.add(item.trim())
      }
    }
  }
  const completedSet = new Set<number>()
  if (Array.isArray(obj.completed_schedules)) {
    for (const item of obj.completed_schedules) {
      if (typeof item === 'number' && Number.isInteger(item) && item > 0) {
        completedSet.add(item)
      }
    }
  }
  const tz = isValidTimezoneId(obj.timezone) ? obj.timezone : DEFAULT_CALENDAR_TIMEZONE
  return {
    claimed_codes: Array.from(claimedSet).slice(-MAX_CLAIMED_CODES),
    completed_schedules: Array.from(completedSet).slice(-MAX_COMPLETED_SCHEDULES),
    timezone: tz,
  }
}

export function mergeCalendarStates(
  local: CalendarStatePayload,
  remote: CalendarStatePayload,
): { merged: CalendarStatePayload; dirty: boolean } {
  const claimed = Array.from(new Set([...remote.claimed_codes, ...local.claimed_codes])).slice(-MAX_CLAIMED_CODES)
  const completed = Array.from(new Set([...remote.completed_schedules, ...local.completed_schedules])).slice(-MAX_COMPLETED_SCHEDULES)
  // 若远端已显式设过非默认时区则沿用远端，否则保留本地已选时区
  const timezone = remote.timezone !== DEFAULT_CALENDAR_TIMEZONE ? remote.timezone : local.timezone
  const merged: CalendarStatePayload = {
    claimed_codes: claimed,
    completed_schedules: completed,
    timezone,
  }
  const dirty = claimed.length !== remote.claimed_codes.length
    || completed.length !== remote.completed_schedules.length
    || timezone !== remote.timezone
  return { merged, dirty }
}

const URGENT_CODE_MS = 24 * 60 * 60 * 1000

export function codeSourceBadge(item: { source_kind?: string; source_count?: number }): {
  label: string
  tone: 'official' | 'verified' | 'community'
} {
  if (item.source_kind === 'community') {
    const count = item.source_count ?? 1
    if (count >= 2) return { label: `×${count} 确认`, tone: 'verified' }
    return { label: '社区整理', tone: 'community' }
  }
  return { label: '官方', tone: 'official' }
}

export function codeServerLabel(server?: string | null): string | null {
  if (server === 'cn') return '国服'
  if (server === 'global') return '国际服'
  if (server === 'all') return '全服'
  return null
}

export function formatCodeExpiry(
  item: {
    expires_at: string | null
    expiry_estimated?: boolean
    published_at?: string | null
    created_at?: string | null
  },
  timeZone: CalendarTimezoneId | string = DEFAULT_CALENDAR_TIMEZONE,
  nowMs: number = Date.now(),
): { text: string; urgent: boolean; estimated: boolean } {
  if (item.expires_at) {
    const endMs = timestamp(item.expires_at)
    const urgent = Number.isFinite(endMs) && endMs > nowMs && endMs - nowMs < URGENT_CODE_MS
    const formatted = formatTimeInZone(item.expires_at, timeZone)
    if (item.expiry_estimated) {
      return { text: `预计 ${formatted} 过期（前瞻短效）`, urgent, estimated: true }
    }
    return { text: `${formatted} 前有效`, urgent, estimated: false }
  }
  const refTime = item.published_at || item.created_at
  const refDay = refTime ? formatTimeInZone(refTime, timeZone, { dateOnly: true }) : ''
  return {
    text: refDay ? `${refDay} 整理 · 未注明有效期` : '未注明有效期',
    urgent: false,
    estimated: false,
  }
}
