<script setup lang="ts">
import { BellRing, Clock3, Mail, Pencil, Plus, Sparkles, X } from 'lucide-vue-next'
import type { PublicContentList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { toRawItem } from '~/utils/public-utils'
import { AVATARS, DEFAULT_AVATAR, avatarGradient, displayName } from '~/utils/avatars'

definePageMeta({ layout: 'account' })
useSeoMeta({ title: '账号中心 · AniSignal' })

const { user, loaded, fetchMe, updateProfile } = useAuth()
const route = useRoute()

onMounted(() => {
  watchEffect(() => {
    if (loaded.value && !user.value)
      navigateTo({ path: '/login', query: { redirect: route.fullPath }, replace: true })
  })
})

// ---------- 概览 ----------
interface Overview {
  email: string
  display_name: string | null
  avatar: string | null
  is_verified: boolean
  created_at: string | null
  last_login_at: string | null
  subscription: { plan: string; label: string; expires_at: string | null }
  keywords_total: number
  keywords_enabled: number
  feed_total: number
  notification_enabled: boolean
}
const overview = ref<Overview | null>(null)

interface NotificationRecord {
  id: number
  kind: 'morning' | 'ai'
  source: 'schedule' | 'manual'
  status: 'pending' | 'sent' | 'skipped' | 'failed'
  subject: string | null
  matched_keywords: string[]
  content_count: number
  error: string | null
  created_at: string
  sent_at: string | null
}
interface NotificationStatus {
  enabled: boolean
  schedule: string
  records: NotificationRecord[]
}
const notificationInfo = ref<NotificationStatus | null>(null)
const notificationBusy = ref('')
const notificationMessage = ref('')
const notificationError = ref('')
let notificationPoll: ReturnType<typeof setTimeout> | undefined

// ---------- 关键词 ----------
interface Keyword { id: number; keyword: string; enabled: boolean }
const keywords = ref<Keyword[]>([])
const newKeyword = ref('')
const keywordError = ref('')
const busy = ref(false)

// ---------- 关注流（每页 20 条）----------
const FEED_PAGE_SIZE = 20
const feed = ref<PublicContentList | null>(null)
const feedPage = ref(1)
const feedTotalPages = computed(() => Math.max(1, Math.ceil((feed.value?.total ?? 0) / FEED_PAGE_SIZE)))

async function fetchFeed() {
  feed.value = await $fetch<PublicContentList>('/api/account/feed', {
    query: { limit: FEED_PAGE_SIZE, offset: (feedPage.value - 1) * FEED_PAGE_SIZE },
  })
}

function feedGoPage(p: number) {
  feedPage.value = Math.min(Math.max(1, p), feedTotalPages.value)
  fetchFeed()
}

async function refresh() {
  try {
    const [ov, kw, fd, nt] = await Promise.all([
      $fetch<Overview>('/api/account/overview'),
      $fetch<Keyword[]>('/api/account/keywords'),
      $fetch<PublicContentList>('/api/account/feed', {
        query: { limit: FEED_PAGE_SIZE, offset: (feedPage.value - 1) * FEED_PAGE_SIZE },
      }),
      $fetch<NotificationStatus>('/api/account/notifications'),
    ])
    overview.value = ov
    keywords.value = kw
    feed.value = fd
    notificationInfo.value = nt
    scheduleNotificationPoll()
  } catch {
    /* 未登录由守卫处理 */
  }
}
onMounted(refresh)
onBeforeUnmount(() => {
  if (notificationPoll) clearTimeout(notificationPoll)
})

function scheduleNotificationPoll() {
  if (notificationPoll) clearTimeout(notificationPoll)
  if (!notificationInfo.value?.records.some(record => record.status === 'pending')) return
  notificationPoll = setTimeout(fetchNotifications, 5000)
}

async function fetchNotifications() {
  notificationInfo.value = await $fetch<NotificationStatus>('/api/account/notifications')
  scheduleNotificationPoll()
}

async function toggleNotifications() {
  if (notificationBusy.value) return
  notificationBusy.value = 'toggle'
  notificationMessage.value = notificationError.value = ''
  try {
    const enabled = !notificationInfo.value?.enabled
    await $fetch('/api/account/notifications', { method: 'PUT', body: { enabled } })
    notificationMessage.value = enabled ? '每日关键词早报已启用。' : '邮件通知已关闭。'
    await refresh()
  } catch (exc: any) {
    notificationError.value = exc?.data?.detail ?? '通知设置保存失败'
  } finally {
    notificationBusy.value = ''
  }
}

async function sendNotification(kind: 'morning' | 'ai') {
  if (notificationBusy.value) return
  notificationBusy.value = kind
  notificationMessage.value = notificationError.value = ''
  try {
    const result = await $fetch<{ message: string }>('/api/account/notifications/send', {
      method: 'POST',
      body: { kind },
    })
    notificationMessage.value = result.message
    await fetchNotifications()
  } catch (exc: any) {
    notificationError.value = exc?.data?.detail ?? '通知任务提交失败'
  } finally {
    notificationBusy.value = ''
  }
}

const notificationStatusLabel: Record<NotificationRecord['status'], string> = {
  pending: '生成中',
  sent: '已发送',
  skipped: '无匹配',
  failed: '失败',
}

async function addKeyword() {
  const keyword = newKeyword.value.trim()
  if (!keyword || busy.value) return
  busy.value = true
  keywordError.value = ''
  try {
    await $fetch('/api/account/keywords', { method: 'POST', body: { keyword } })
    newKeyword.value = ''
    await refresh()
  } catch (exc: any) {
    keywordError.value = exc?.data?.detail ?? '添加失败'
  } finally {
    busy.value = false
  }
}

async function toggleKeyword(k: Keyword) {
  busy.value = true
  try {
    await $fetch(`/api/account/keywords/${k.id}`, { method: 'PUT', body: { enabled: !k.enabled } })
    await refresh()
  } finally {
    busy.value = false
  }
}

async function removeKeyword(k: Keyword) {
  busy.value = true
  try {
    await $fetch(`/api/account/keywords/${k.id}`, { method: 'DELETE' })
    await refresh()
  } finally {
    busy.value = false
  }
}

// ---------- 资料编辑 ----------
const editingProfile = ref(false)
const nameDraft = ref('')
const avatarDraft = ref(DEFAULT_AVATAR)

function startEditProfile() {
  nameDraft.value = user.value?.display_name ?? ''
  avatarDraft.value = user.value?.avatar ?? DEFAULT_AVATAR
  editingProfile.value = true
}

async function saveProfile() {
  busy.value = true
  try {
    await updateProfile(nameDraft.value.trim(), avatarDraft.value)
    editingProfile.value = false
    await refresh()
  } catch (exc: any) {
    keywordError.value = exc?.data?.detail ?? '保存失败'
  } finally {
    busy.value = false
  }
}

// ---------- 安全 ----------
const oldPassword = ref('')
const newPassword = ref('')
const securityMsg = ref('')
const securityErr = ref('')

async function changePassword() {
  if (busy.value) return
  busy.value = true
  securityErr.value = securityMsg.value = ''
  try {
    await $fetch('/api/auth/password/change', {
      method: 'POST',
      body: { old_password: oldPassword.value, new_password: newPassword.value },
    })
    securityMsg.value = '密码已修改，其他设备已退出。'
    oldPassword.value = newPassword.value = ''
    await fetchMe()
  } catch (exc: any) {
    securityErr.value = exc?.data?.detail ?? '修改失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="space-y-5">
    <!-- 账号信息 -->
    <section class="panel p-5 sm:p-6">
      <div class="flex flex-wrap items-center gap-4">
        <span class="grid size-14 shrink-0 place-items-center rounded-2xl bg-gradient-to-b text-xl font-semibold text-white shadow-inner" :class="avatarGradient(overview?.avatar ?? user?.avatar)">
          {{ displayName(overview ?? user)[0]!.toUpperCase() }}
        </span>
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <h1 class="truncate text-lg font-semibold">{{ displayName(overview ?? user) }}</h1>
            <button class="chip bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400" @click="startEditProfile">
              <Pencil :size="11" /> 编辑资料
            </button>
            <span v-if="overview?.is_verified" class="chip bg-emerald-500/10 font-medium text-emerald-600 dark:text-emerald-400">邮箱已验证</span>
            <span class="chip bg-blue-500/10 font-medium text-blue-700 dark:text-blue-300">{{ overview?.subscription.label ?? '免费版' }}</span>
          </div>
          <p class="mono mt-1 text-xs text-slate-400 dark:text-zinc-500">
            {{ user?.email }} · 注册于 {{ fmtTime(overview?.created_at) }} · 最近登录 {{ fmtTime(overview?.last_login_at) }}
            <template v-if="overview?.subscription.expires_at"> · 到期 {{ overview.subscription.expires_at }}</template>
            <template v-else> · 无到期时间</template>
          </p>
        </div>
      </div>

      <!-- 资料编辑 -->
      <div v-if="editingProfile" class="mt-5 rounded-xl border border-blue-500/30 bg-blue-500/5 p-4">
        <h3 class="text-sm font-semibold">编辑资料</h3>
        <label class="mt-3 block text-xs font-medium text-slate-500 dark:text-zinc-400">昵称（留空则使用邮箱前缀）</label>
        <input v-model="nameDraft" maxlength="24" placeholder="最多 24 个字符"
          class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        <p class="mt-3 block text-xs font-medium text-slate-500 dark:text-zinc-400">头像</p>
        <div class="mt-2 grid grid-cols-6 gap-2">
          <button
            v-for="a in AVATARS"
            :key="a.id"
            type="button"
            :title="a.label"
            class="grid aspect-square place-items-center rounded-xl bg-gradient-to-b text-sm font-semibold text-white transition-transform hover:scale-105"
            :class="[a.gradient, avatarDraft === a.id ? 'ring-2 ring-blue-500 ring-offset-2 dark:ring-offset-zinc-900' : '']"
            @click="avatarDraft = a.id"
          >
            {{ displayName(overview ?? user)[0]!.toUpperCase() }}
          </button>
        </div>
        <div class="mt-4 flex gap-2">
          <button class="btn-primary px-4 py-2" :disabled="busy" @click="saveProfile">保存</button>
          <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" @click="editingProfile = false">取消</button>
        </div>
      </div>

      <!-- 关键指标 -->
      <div class="mt-5 grid grid-cols-3 gap-3">
        <div class="rounded-xl bg-slate-500/5 px-4 py-3 dark:bg-white/5">
          <p class="text-xs text-slate-500 dark:text-zinc-400">监控关键词</p>
          <p class="mono mt-1 text-xl font-semibold">{{ overview?.keywords_total ?? '—' }}<span class="text-xs font-normal text-slate-400"> / 10</span></p>
        </div>
        <div class="rounded-xl bg-slate-500/5 px-4 py-3 dark:bg-white/5">
          <p class="text-xs text-slate-500 dark:text-zinc-400">关注内容</p>
          <p class="mono mt-1 text-xl font-semibold">{{ overview?.feed_total ?? '—' }}</p>
        </div>
        <div class="rounded-xl bg-slate-500/5 px-4 py-3 dark:bg-white/5">
          <p class="text-xs text-slate-500 dark:text-zinc-400">订阅状态</p>
          <p class="mt-1 text-xl font-semibold">{{ overview?.subscription.label ?? '免费版' }}</p>
        </div>
      </div>
    </section>

    <div class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div class="space-y-5">
        <!-- 关键词监控 -->
        <section class="panel p-5">
          <div class="flex items-center justify-between gap-2">
            <h2 class="text-sm font-semibold">关键词监控</h2>
            <span class="text-xs text-slate-400">{{ overview?.keywords_enabled ?? 0 }} 个启用中</span>
          </div>
          <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
            关键词用于生成下方「我的关注流」和邮件早报；只有启用中的关键词会参与通知匹配。
          </p>

          <form class="mt-3 flex gap-2" @submit.prevent="addKeyword">
            <div class="relative flex-1">
              <Plus :size="14" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input v-model="newKeyword" placeholder="添加关键词，如 爱弥斯 / 菲比 / 3.6版本" maxlength="64"
                class="w-full rounded-md border border-slate-900/10 bg-white/70 py-2 pl-8 pr-3 text-sm dark:border-white/15 dark:bg-white/5">
            </div>
            <button type="submit" class="btn-primary shrink-0 px-4 py-2" :disabled="busy || !newKeyword.trim()">添加</button>
          </form>
          <p v-if="keywordError" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ keywordError }}</p>

          <div class="mt-3 flex flex-wrap gap-2">
            <div v-if="!keywords.length" class="w-full py-6 text-center text-sm text-slate-400 dark:text-zinc-500">
              还没有关键词，添加一个开始构建你的关注流。
            </div>
            <div
              v-for="k in keywords"
              :key="k.id"
              class="group flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors"
              :class="k.enabled
                ? 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                : 'border-slate-900/10 text-slate-400 line-through dark:border-white/15 dark:text-zinc-500'"
            >
              <button :disabled="busy" :title="k.enabled ? '点击暂停' : '点击启用'" @click="toggleKeyword(k)">
                <span class="size-1.5 rounded-full" :class="k.enabled ? 'bg-blue-500' : 'bg-slate-400'" />
              </button>
              <span>{{ k.keyword }}</span>
              <button :disabled="busy" class="opacity-40 transition-opacity group-hover:opacity-100" title="删除" @click="removeKeyword(k)">
                <X :size="13" />
              </button>
            </div>
          </div>
        </section>

        <!-- 我的关注流 -->
        <section class="panel overflow-hidden">
          <div class="flex items-center justify-between px-5 py-3.5">
            <h2 class="text-sm font-semibold">我的关注流</h2>
            <span class="text-xs text-slate-400">{{ feed?.total ?? 0 }} 条命中</span>
          </div>
          <div v-if="feed?.items?.length" class="border-t border-slate-900/5 dark:border-white/5">
            <RawFeedRow v-for="item in feed.items" :key="item.id" :item="toRawItem(item)" />
          </div>
          <p v-else class="border-t border-slate-900/5 px-5 py-8 text-center text-sm text-slate-400 dark:border-white/5 dark:text-zinc-500">
            {{ keywords.length ? '暂无命中关键词的内容，系统持续采集中。' : '添加关键词后，这里会展示命中的最新内容。' }}
          </p>
          <div v-if="(feed?.total ?? 0) > FEED_PAGE_SIZE" class="flex items-center justify-between border-t border-slate-900/5 px-5 py-2.5 text-xs text-slate-400 dark:border-white/5 dark:text-zinc-500">
            <span>共 {{ feed?.total }} 条 · 每页 {{ FEED_PAGE_SIZE }} 条</span>
            <div class="flex items-center gap-3">
              <button :disabled="feedPage <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="feedGoPage(feedPage - 1)">« 上一页</button>
              <span class="mono">{{ feedPage }} / {{ feedTotalPages }}</span>
              <button :disabled="feedPage >= feedTotalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="feedGoPage(feedPage + 1)">下一页 »</button>
            </div>
          </div>
        </section>
      </div>

      <!-- 右栏：通知 + 订阅 + 安全 -->
      <div class="space-y-5">
        <section class="panel p-5">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="flex items-center gap-2 text-sm font-semibold"><BellRing :size="15" /> 邮件通知</h2>
              <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">{{ notificationInfo?.schedule ?? '每天 08:00（北京时间）' }}</p>
            </div>
            <button
              type="button"
              role="switch"
              :aria-checked="notificationInfo?.enabled ?? false"
              :aria-label="notificationInfo?.enabled ? '关闭邮件通知' : '启用邮件通知'"
              class="relative h-7 w-12 shrink-0 cursor-pointer rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
              :class="notificationInfo?.enabled ? 'bg-blue-600' : 'bg-slate-300 dark:bg-zinc-700'"
              :disabled="!!notificationBusy || !overview?.is_verified"
              @click="toggleNotifications"
            >
              <span class="absolute top-1 size-5 rounded-full bg-white shadow-sm transition-transform duration-200" :class="notificationInfo?.enabled ? 'translate-x-6' : 'translate-x-1'" />
            </button>
          </div>

          <p class="mt-3 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
            仅发送明确命中已启用关键词的内容；标题和采集线索无法确定时，系统会用 AI 复核，拿不准则不发送。
          </p>

          <div class="mt-4 grid gap-2">
            <button
              type="button"
              class="btn-primary inline-flex min-h-11 w-full cursor-pointer items-center justify-center gap-2 px-4 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              :disabled="!!notificationBusy || !notificationInfo?.enabled || !(overview?.keywords_enabled ?? 0)"
              @click="sendNotification('morning')"
            >
              <Mail :size="15" /> {{ notificationBusy === 'morning' ? '提交中…' : '发送今日早报' }}
            </button>
            <button
              type="button"
              class="inline-flex min-h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-md border border-slate-900/10 px-4 py-2 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/15 dark:text-blue-300"
              :disabled="!!notificationBusy || !notificationInfo?.enabled || !(overview?.keywords_enabled ?? 0)"
              @click="sendNotification('ai')"
            >
              <Sparkles :size="15" /> {{ notificationBusy === 'ai' ? '提交中…' : '生成 AI 精炼通知' }}
            </button>
          </div>
          <p class="mt-2 flex items-start gap-1.5 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
            <Clock3 :size="13" class="mt-0.5 shrink-0" /> AI 会复核并总结最近 24 小时内容，通常需要几分钟，完成后自动发到当前邮箱。
          </p>
          <p v-if="notificationMessage" class="mt-3 text-xs text-emerald-600 dark:text-emerald-400">{{ notificationMessage }}</p>
          <p v-if="notificationError" class="mt-3 text-xs text-red-600 dark:text-red-400">{{ notificationError }}</p>

          <div v-if="notificationInfo?.records.length" class="mt-4 border-t border-slate-900/5 pt-3 dark:border-white/5">
            <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">最近发送</p>
            <div class="mt-2 space-y-2">
              <div v-for="record in notificationInfo.records.slice(0, 3)" :key="record.id" class="rounded-lg bg-slate-500/5 px-3 py-2 dark:bg-white/5">
                <div class="flex items-center justify-between gap-2 text-xs">
                  <span class="font-medium">{{ record.kind === 'ai' ? 'AI 精炼通知' : '关键词早报' }}</span>
                  <span
                    :class="record.status === 'sent' ? 'text-emerald-600 dark:text-emerald-400' : record.status === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-slate-400'"
                  >{{ notificationStatusLabel[record.status] }}</span>
                </div>
                <p class="mono mt-1 text-[11px] text-slate-400 dark:text-zinc-500">{{ fmtTime(record.sent_at ?? record.created_at) }} · {{ record.content_count }} 条内容</p>
                <p v-if="record.error" class="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-zinc-400">{{ record.error }}</p>
              </div>
            </div>
          </div>
        </section>

        <section class="panel p-5">
          <h2 class="text-sm font-semibold">订阅</h2>
          <div class="mt-3 rounded-xl border border-blue-500/30 bg-blue-500/5 p-3.5">
            <div class="flex items-baseline justify-between">
              <p class="text-sm font-medium text-blue-700 dark:text-blue-300">免费版（当前）</p>
              <p class="mono text-xs text-slate-400">永久免费</p>
            </div>
            <p class="mt-1.5 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
              全站内容免费浏览，含本页全部功能；无到期时间。
            </p>
          </div>
          <div class="mt-2.5 rounded-xl border border-dashed border-slate-900/15 p-3.5 dark:border-white/15">
            <div class="flex items-baseline justify-between">
              <p class="text-sm font-medium text-slate-600 dark:text-zinc-300">基础订阅（规划中）</p>
              <p class="mono text-xs text-slate-400">¥3 / 30 天</p>
            </div>
            <p class="mt-1.5 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
              每日 08:00 关键词早报已经开放测试；上线前全站免费，不收任何费用。
            </p>
          </div>
        </section>

        <section class="panel p-5">
          <h2 class="text-sm font-semibold">安全设置</h2>
          <input v-model="oldPassword" type="password" autocomplete="current-password" placeholder="原密码"
            class="mt-3 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="新密码（至少 8 位）"
            class="mt-2.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <p v-if="securityErr" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ securityErr }}</p>
          <p v-if="securityMsg" class="mt-2 text-xs text-emerald-600 dark:text-emerald-400">{{ securityMsg }}</p>
          <button class="btn-primary mt-3 w-full px-4 py-2" :disabled="busy || !oldPassword || !newPassword" @click="changePassword">
            {{ busy ? '提交中…' : '修改密码' }}
          </button>
          <p class="mt-2 text-center text-xs text-slate-400 dark:text-zinc-500">修改后其他设备将自动退出</p>
        </section>
      </div>
    </div>
  </div>
</template>
