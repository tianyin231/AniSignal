/**
 * 账号中心的共享状态：页面 provide，各标签页组件 inject。
 * 状态随页面卸载释放，退出登录后不会残留上一个账号的数据。
 */
import type { InjectionKey } from 'vue'
import type { PublicContentList } from '~/composables/usePublicApi'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'

export interface AccountOverview {
  email: string
  display_name: string | null
  avatar: string | null
  is_verified: boolean
  created_at: string | null
  last_login_at: string | null
  keywords_total: number
  keywords_enabled: number
  feed_total: number
}
export interface AccountKeyword { id: number, keyword: string, enabled: boolean, matches: number }
export type NotificationState = 'pending' | 'sending' | 'sent' | 'skipped' | 'failed'
export interface NotificationRecord {
  id: number
  kind: string
  source: string
  status: NotificationState
  subject: string | null
  matched_keywords: string[]
  content_count: number
  error: string | null
  created_at: string
  sent_at: string | null
}
export interface NotificationStatus { enabled: boolean, instant_enabled: boolean, schedule: string, records: NotificationRecord[] }
export interface CustomFeedRow {
  id: number
  name: string
  kind: 'uid' | 'keyword' | string
  platform: string
  target_value: string
  min_views: number
  push_enabled: boolean
  is_public: boolean
  enabled: boolean
  status: string
  item_count: number
  last_collected_at: string | null
  /** 上次采集失败的友好原因；成功后为 null */
  last_error?: string | null
  interval_minutes: number
  filters: { include?: string[], exclude?: string[] }
}
export interface CustomQuota { admitted: boolean, used: number, max_feeds: number, min_interval_minutes: number }

export const ACCOUNT_TABS = [
  { key: 'feed', label: '关注流' },
  { key: 'notify', label: '通知' },
  { key: 'custom', label: '定制监控' },
  { key: 'profile', label: '账号与安全' },
] as const
export type AccountTab = typeof ACCOUNT_TABS[number]['key']
export const MAX_KEYWORDS = 10
export const FEED_PAGE_SIZE = 20

type Section = 'overview' | 'keywords' | 'feed' | 'notifications' | 'custom'
const SECTION_LABELS: Record<Section, string> = {
  overview: '账号概览', keywords: '关键词', feed: '关注流', notifications: '通知状态', custom: '定制监控',
}

export function createAccountCenter() {
  const { user } = useAuth()
  const overview = ref<AccountOverview | null>(null)
  const keywords = ref<AccountKeyword[]>([])
  const notifications = ref<NotificationStatus | null>(null)
  const customFeeds = ref<CustomFeedRow[]>([])
  const customQuota = ref<CustomQuota>({ admitted: false, used: 0, max_feeds: 3, min_interval_minutes: 60 })
  const loading = reactive<Record<Section, boolean>>({ overview: true, keywords: true, feed: true, notifications: true, custom: true })
  const errors = reactive<Record<Section, string>>({ overview: '', keywords: '', feed: '', notifications: '', custom: '' })
  let active = true

  /** 会话过期（401）：清空登录态，页面随即带着当前位置跳去登录。 */
  function failure(exc: unknown, fallback: string): string {
    if (apiErrorStatus(exc) === 401) user.value = null
    return apiErrorDetail(exc, fallback)
  }

  async function load(section: Section, run: () => Promise<void>): Promise<boolean> {
    loading[section] = true
    errors[section] = ''
    try {
      await run()
      return true
    } catch (exc) {
      failure(exc, '')
      errors[section] = `${SECTION_LABELS[section]}暂时加载失败，请重试。`
      return false
    } finally {
      loading[section] = false
    }
  }

  // ---------- 概览 / 关键词 ----------
  const fetchOverview = () => load('overview', async () => { overview.value = await $fetch<AccountOverview>('/api/account/overview') })
  const fetchKeywords = () => load('keywords', async () => { keywords.value = await $fetch<AccountKeyword[]>('/api/account/keywords') })
  const enabledKeywords = computed(() => keywords.value.filter(k => k.enabled))
  const keywordBusy = ref(false)
  const keywordError = ref('')

  async function mutateKeywords(run: () => Promise<unknown>, fallback: string) {
    if (keywordBusy.value) return false
    keywordBusy.value = true
    keywordError.value = ''
    try {
      await run()
      await Promise.all([fetchKeywords(), fetchOverview(), fetchFeed()])
      return true
    } catch (exc) {
      keywordError.value = failure(exc, fallback)
      return false
    } finally {
      keywordBusy.value = false
    }
  }
  const addKeyword = (keyword: string) =>
    mutateKeywords(() => $fetch('/api/account/keywords', { method: 'POST', body: { keyword } }), '添加失败，请重试。')
  const toggleKeyword = (k: AccountKeyword) =>
    mutateKeywords(() => $fetch(`/api/account/keywords/${k.id}`, { method: 'PUT', body: { enabled: !k.enabled } }), '关键词更新失败，请重试。')
  const removeKeyword = (k: AccountKeyword) =>
    mutateKeywords(() => $fetch(`/api/account/keywords/${k.id}`, { method: 'DELETE' }), '关键词删除失败，请重试。')

  // ---------- 关注流（每页 20 条，可按关键词筛选）----------
  const feed = ref<PublicContentList | null>(null)
  const feedPage = ref(1)
  const feedKeywordId = ref<number | null>(null)
  const feedTotalPages = computed(() => Math.max(1, Math.ceil((feed.value?.total ?? 0) / FEED_PAGE_SIZE)))
  let feedController: AbortController | undefined

  async function fetchFeed() {
    feedController?.abort()
    const controller = new AbortController()
    feedController = controller
    loading.feed = true
    errors.feed = ''
    try {
      const result = await $fetch<PublicContentList>('/api/account/feed', {
        query: {
          limit: FEED_PAGE_SIZE,
          offset: (feedPage.value - 1) * FEED_PAGE_SIZE,
          ...(feedKeywordId.value ? { keyword_id: feedKeywordId.value } : {}),
        },
        signal: controller.signal,
      })
      if (!controller.signal.aborted) feed.value = result
    } catch (exc) {
      if (!controller.signal.aborted) {
        failure(exc, '')
        errors.feed = '关注流暂时加载失败，请重试。'
      }
    } finally {
      if (feedController === controller) {
        loading.feed = false
        feedController = undefined
      }
    }
  }
  function filterFeed(id: number | null) {
    feedKeywordId.value = id
    feedPage.value = 1
    return fetchFeed()
  }
  function feedGoPage(page: number) {
    feedPage.value = Math.min(Math.max(1, page), feedTotalPages.value)
    return fetchFeed()
  }
  // 被筛选的关键词停用 / 删除后回到全部
  watch(keywords, (list) => {
    if (feedKeywordId.value && !list.some(k => k.id === feedKeywordId.value && k.enabled)) filterFeed(null)
  })

  // ---------- 邮件通知 ----------
  const notifyBusy = ref<'' | 'toggle' | 'instant' | 'morning' | 'ai'>('')
  const notifyMessage = ref('')
  const notifyError = ref('')
  let notificationPoll: ReturnType<typeof setTimeout> | undefined

  function scheduleNotificationPoll() {
    if (notificationPoll) clearTimeout(notificationPoll)
    if (!active || !notifications.value?.records.some(r => r.status === 'pending' || r.status === 'sending')) return
    notificationPoll = setTimeout(fetchNotifications, 5000)
  }
  async function fetchNotifications() {
    const ok = await load('notifications', async () => {
      notifications.value = await $fetch<NotificationStatus>('/api/account/notifications')
    })
    if (ok) scheduleNotificationPoll()
  }
  const notificationReady = computed(() => Boolean(notifications.value?.enabled && !loading.notifications && !errors.notifications))
  const aiReady = computed(() => notificationReady.value && !loading.keywords && !errors.keywords && enabledKeywords.value.length > 0)
  const notificationBlockReason = computed(() => {
    if (loading.notifications || errors.notifications) return ''
    if (!notifications.value?.enabled) return '开启邮件通知后才能接收早报，也才能手动发送。'
    if (!loading.keywords && !errors.keywords && !enabledKeywords.value.length) return '每日早报已开启；AI 精炼和重要变化提醒需要先添加关键词。'
    return ''
  })

  async function updateNotifications(kind: 'toggle' | 'instant', body: Record<string, boolean>, okText: string) {
    if (notifyBusy.value) return
    notifyBusy.value = kind
    notifyMessage.value = notifyError.value = ''
    try {
      await $fetch('/api/account/notifications', { method: 'PUT', body })
      notifyMessage.value = okText
      await fetchNotifications()
    } catch (exc) {
      notifyError.value = failure(exc, '通知设置保存失败，请重试。')
    } finally {
      notifyBusy.value = ''
    }
  }
  function toggleEmail() {
    const enabled = !notifications.value?.enabled
    return updateNotifications('toggle', { enabled }, enabled
      ? '邮件通知已开启，每日早报会按个人偏好筛选。'
      : '邮件通知已关闭，早报、重要变化和定制监控邮件均已暂停，偏好会保留。')
  }
  function toggleInstant() {
    const instant_enabled = !notifications.value?.instant_enabled
    return updateNotifications('instant', { instant_enabled }, instant_enabled ? '重要变化提醒已开启，只提醒之后的新变化。' : '重要变化提醒已关闭。')
  }
  async function sendNotification(kind: 'morning' | 'ai') {
    if (notifyBusy.value) return
    notifyBusy.value = kind
    notifyMessage.value = notifyError.value = ''
    try {
      const result = await $fetch<{ message: string }>('/api/account/notifications/send', { method: 'POST', body: { kind } })
      notifyMessage.value = result.message
      await fetchNotifications()
    } catch (exc) {
      notifyError.value = failure(exc, '通知任务提交失败，请重试。')
    } finally {
      notifyBusy.value = ''
    }
  }

  // ---------- 定制监控 ----------
  const customBusy = ref(false)
  const customError = ref('')
  const customMessage = ref('')
  const fetchCustom = () => load('custom', async () => {
    const [feeds, quota] = await Promise.all([
      $fetch<CustomFeedRow[]>('/api/account/custom-feeds'),
      $fetch<CustomQuota>('/api/account/custom-quota'),
    ])
    customFeeds.value = feeds
    customQuota.value = quota
  })
  async function mutateCustom(run: () => Promise<void>, fallback: string, okText: string) {
    if (customBusy.value) return false
    customBusy.value = true
    customError.value = customMessage.value = ''
    try {
      await run()
      customMessage.value = okText
      return true
    } catch (exc) {
      customError.value = failure(exc, fallback)
      return false
    } finally {
      customBusy.value = false
    }
  }
  const createCustom = (body: Record<string, unknown>) => mutateCustom(async () => {
    await $fetch('/api/account/custom-feeds', { method: 'POST', body })
    await fetchCustom()
  }, '创建失败，请检查后重试。', '监控已创建，后台会开始采集。')
  const patchCustom = (row: CustomFeedRow, body: Record<string, unknown>, okText: string) => mutateCustom(async () => {
    const res = await $fetch<{ target_value?: string }>(`/api/account/custom-feeds/${row.id}`, { method: 'PATCH', body })
    Object.assign(row, body, res?.target_value ? { target_value: res.target_value } : {})
  }, '操作失败，请重试。', okText)
  const triggerCustom = (row: CustomFeedRow) => mutateCustom(async () => {
    await $fetch(`/api/account/custom-feeds/${row.id}/collect`, { method: 'POST' })
  }, '手动刷新失败，请稍后重试。', `「${row.name}」已加入采集队列，稍后自动更新。`)
  const removeCustom = (row: CustomFeedRow) => mutateCustom(async () => {
    await $fetch(`/api/account/custom-feeds/${row.id}`, { method: 'DELETE' })
    customFeeds.value = customFeeds.value.filter(f => f.id !== row.id)
    customQuota.value.used = Math.max(0, customQuota.value.used - 1)
  }, '删除失败，请重试。', '监控已删除。')

  function loadAll() {
    return Promise.all([fetchOverview(), fetchKeywords(), fetchNotifications(), fetchCustom()])
  }
  function dispose() {
    active = false
    feedController?.abort()
    if (notificationPoll) clearTimeout(notificationPoll)
  }

  return {
    user, overview, keywords, enabledKeywords, notifications, customFeeds, customQuota, loading, errors,
    fetchOverview, fetchKeywords, keywordBusy, keywordError, addKeyword, toggleKeyword, removeKeyword,
    feed, feedPage, feedKeywordId, feedTotalPages, fetchFeed, filterFeed, feedGoPage,
    notifyBusy, notifyMessage, notifyError, fetchNotifications, notificationReady, aiReady, notificationBlockReason,
    toggleEmail, toggleInstant, sendNotification,
    customBusy, customError, customMessage, fetchCustom, createCustom, patchCustom, triggerCustom, removeCustom,
    loadAll, dispose,
  }
}

export type AccountCenter = ReturnType<typeof createAccountCenter>
export const ACCOUNT_CENTER: InjectionKey<AccountCenter> = Symbol('account-center')

export function useAccountCenter(): AccountCenter {
  const center = inject(ACCOUNT_CENTER)
  if (!center) throw new Error('useAccountCenter() 只能在账号中心页面内使用')
  return center
}
