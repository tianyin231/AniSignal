/**
 * 活动日历个人状态：兑换码「已领」、日程「已完成」、时区偏好。
 * - 访客持久化到 localStorage；
 * - 已登录账号自动与 `/api/account/calendar-state` 双向同步（登录时合并本地打卡，不丢数据）；
 * - SSR 阶段始终使用默认空状态 + 北京时间，客户端 onMounted 后水合，杜绝水合不一致。
 */

import {
  DEFAULT_CALENDAR_TIMEZONE,
  isValidTimezoneId,
  type CalendarTimezoneId,
} from '~/utils/calendar'
import {
  MAX_CLAIMED_CODES,
  MAX_COMPLETED_SCHEDULES,
  makeCodeKey,
  mergeCalendarStates,
  normalizeCalendarState,
  type CalendarStatePayload,
} from '~/utils/calendar-state'

const STORAGE_KEY = 'anisignal:calendar-state:v1'

let syncTimer: ReturnType<typeof setTimeout> | undefined
let pendingSync: Promise<void> | null = null

export function useCalendarState() {
  const claimedCodes = useState<string[]>('calendar-claimed-codes', () => [])
  const completedSchedules = useState<number[]>('calendar-completed-schedules', () => [])
  const timezone = useState<CalendarTimezoneId>('calendar-timezone', () => DEFAULT_CALENDAR_TIMEZONE)
  const hydrated = useState<boolean>('calendar-state-hydrated', () => false)
  const syncedUserId = useState<number | null>('calendar-state-synced-uid', () => null)

  const { user } = useAuth()

  const claimedSet = computed(() => new Set(claimedCodes.value))
  const completedSet = computed(() => new Set(completedSchedules.value))

  function currentPayload(): CalendarStatePayload {
    return {
      claimed_codes: claimedCodes.value.slice(-MAX_CLAIMED_CODES),
      completed_schedules: completedSchedules.value.slice(-MAX_COMPLETED_SCHEDULES),
      timezone: timezone.value,
    }
  }

  function applyPayload(payload: CalendarStatePayload) {
    claimedCodes.value = payload.claimed_codes
    completedSchedules.value = payload.completed_schedules
    timezone.value = payload.timezone
  }

  function persistLocal() {
    if (!import.meta.client) return
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(currentPayload()))
    } catch { /* 隐私模式静默忽略 */ }
  }

  async function pushRemoteNow() {
    if (!import.meta.client || !user.value) return
    try {
      await $fetch('/api/account/calendar-state', {
        method: 'PUT',
        body: currentPayload(),
      })
    } catch { /* 网络或会话过期时本地仍已保留 */ }
  }

  function scheduleRemoteSync() {
    if (!import.meta.client || !user.value) return
    clearTimeout(syncTimer)
    syncTimer = setTimeout(() => { void pushRemoteNow() }, 350)
  }

  function hydrateLocal() {
    if (!import.meta.client || hydrated.value) return
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) applyPayload(normalizeCalendarState(JSON.parse(raw)))
    } catch { /* 忽略损坏存储 */ }
    hydrated.value = true
  }

  function syncFromAccount(uid: number): Promise<void> {
    if (!import.meta.client) return Promise.resolve()
    if (syncedUserId.value === uid) return Promise.resolve()
    if (pendingSync) return pendingSync
    syncedUserId.value = uid
    const run = (async () => {
      try {
        const remoteRaw = await $fetch<CalendarStatePayload>('/api/account/calendar-state')
        const remote = normalizeCalendarState(remoteRaw)
        const { merged, dirty } = mergeCalendarStates(currentPayload(), remote)
        applyPayload(merged)
        persistLocal()
        if (dirty) await pushRemoteNow()
      } catch {
        syncedUserId.value = null
      }
    })()
    pendingSync = run
    run.finally(() => { pendingSync = null })
    return run
  }

  if (import.meta.client) {
    onMounted(() => {
      hydrateLocal()
      if (user.value?.id) void syncFromAccount(user.value.id)
    })

    watch(() => user.value?.id ?? null, (uid) => {
      if (!uid) {
        syncedUserId.value = null
        return
      }
      hydrateLocal()
      void syncFromAccount(uid)
    })
  }

  function isCodeClaimed(code: string, community?: string | null): boolean {
    const key = makeCodeKey(code, community)
    return claimedSet.value.has(key) || claimedSet.value.has(code.trim().toUpperCase())
  }

  function toggleCodeClaimed(code: string, community?: string | null) {
    const key = makeCodeKey(code, community)
    const bare = code.trim().toUpperCase()
    const next = new Set(claimedCodes.value)
    if (next.has(key) || next.has(bare)) {
      next.delete(key)
      next.delete(bare)
    } else {
      next.add(key)
    }
    claimedCodes.value = Array.from(next).slice(-MAX_CLAIMED_CODES)
    persistLocal()
    scheduleRemoteSync()
  }

  function isScheduleCompleted(scheduleId: number): boolean {
    return completedSet.value.has(scheduleId)
  }

  function toggleScheduleCompleted(scheduleId: number) {
    const next = new Set(completedSchedules.value)
    if (next.has(scheduleId)) next.delete(scheduleId)
    else next.add(scheduleId)
    completedSchedules.value = Array.from(next).slice(-MAX_COMPLETED_SCHEDULES)
    persistLocal()
    scheduleRemoteSync()
  }

  function setTimezone(tz: CalendarTimezoneId) {
    if (!isValidTimezoneId(tz)) return
    timezone.value = tz
    persistLocal()
    scheduleRemoteSync()
  }

  return {
    claimedCodes,
    completedSchedules,
    timezone,
    hydrated,
    isSynced: computed(() => Boolean(user.value)),
    isCodeClaimed,
    toggleCodeClaimed,
    isScheduleCompleted,
    toggleScheduleCompleted,
    setTimezone,
  }
}
