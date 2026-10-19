<script setup lang="ts">
import { BellRing, CircleAlert, Clock3, KeyRound, Mail, Pencil, Plus, Sparkles, X } from 'lucide-vue-next'
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
  keywords_total: number
  keywords_enabled: number
  feed_total: number
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

const notificationReady = computed(() => Boolean(notificationInfo.value?.enabled))
const aiNotificationReady = computed(() => Boolean(
  notificationReady.value
  && (overview.value?.keywords_enabled ?? 0) > 0,
))
const notificationBlockReason = computed(() => {
  // 登录用户必然已验证（login/register 流程保证），此处只提示通知链路自身的前置条件
  if (!notificationInfo.value?.enabled) return '请先开启邮件通知，之后才能手动发送。'
  if (!(overview.value?.keywords_enabled ?? 0)) return '全站早报已开启。AI 精炼通知需先添加关键词。'
  return ''
})

// ---------- 关键词 ----------
interface Keyword { id: number; keyword: string; enabled: boolean; matches: number }
const keywords = ref<Keyword[]>([])
const newKeyword = ref('')
const keywordError = ref('')
const busy = ref(false)

// ---------- 关注流（每页 20 条，可按关键词筛选）----------
const FEED_PAGE_SIZE = 20
// 定制监控（UID/关键词，可公开可推送）
interface CustomFeedRow {
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
}
const customFeeds = ref<CustomFeedRow[]>([])
const customError = ref('')
const customForm = reactive({ name: '', kind: 'keyword', target_value: '', min_views: 0, push_enabled: false, is_public: false })
const customBusy = ref(false)

async function refreshCustomFeeds() {
  try {
    customFeeds.value = await $fetch<CustomFeedRow[]>('/api/account/custom-feeds')
  } catch { /* 未登录等场景静默 */ }
}

async function createCustomFeed() {
  if (customBusy.value) return
  try {
    await $fetch('/api/account/custom-feeds', {
      method: 'POST',
      body: { ...customForm, min_views: Number(customForm.min_views) || 0 },
    })
    customError.value = ''
    customForm.name = ''
    customForm.target_value = ''
    customForm.min_views = 0
    await refreshCustomFeeds()
  } catch (exc: any) {
    customError.value = exc?.data?.detail ?? '创建失败'
  }
}

async function patchCustomFeed(feed: CustomFeedRow, body: Record<string, unknown>, okText: string) {
  if (customBusy.value) return
  try {
    await $fetch(`/api/account/custom-feeds/${feed.id}`, { method: 'PATCH', body })
    Object.assign(feed, body)
    customError.value = ''
  } catch (exc: any) {
    customError.value = exc?.data?.detail ?? '操作失败'
  }
}

async function removeCustomFeed(feed: CustomFeedRow) {
  if (customBusy.value || !confirm(`删除监控「${feed.name}」及其全部抓取记录？`)) return
  try {
    await $fetch(`/api/account/custom-feeds/${feed.id}`, { method: 'DELETE' })
    customFeeds.value = customFeeds.value.filter(f => f.id !== feed.id)
    customError.value = ''
  } catch (exc: any) {
    customError.value = exc?.data?.detail ?? '删除失败'
  }
}

const feed = ref<PublicContentList | null>(null)
const feedPage = ref(1)
const selectedKeywordId = ref<number | null>(null)
const feedTotalPages = computed(() => Math.max(1, Math.ceil((feed.value?.total ?? 0) / FEED_PAGE_SIZE)))
const selectedKeyword = computed(() => keywords.value.find(k => k.id === selectedKeywordId.value) ?? null)

const feedQuery = computed(() => ({
  limit: FEED_PAGE_SIZE,
  offset: (feedPage.value - 1) * FEED_PAGE_SIZE,
  ...(selectedKeywordId.value ? { keyword_id: selectedKeywordId.value } : {}),
}))

async function fetchFeed() {
  feed.value = await $fetch<PublicContentList>('/api/account/feed', { query: feedQuery.value })
}

function setKeywordFilter(k: Keyword) {
  if (!k.enabled || busy.value) return
  selectedKeywordId.value = selectedKeywordId.value === k.id ? null : k.id
  feedPage.value = 1
  fetchFeed()
}

function clearKeywordFilter() {
  selectedKeywordId.value = null
  feedPage.value = 1
  fetchFeed()
}

// 被筛选的关键词停用/删除后，自动回到全部
watch(keywords, (list) => {
  if (selectedKeywordId.value && !list.some(k => k.id === selectedKeywordId.value && k.enabled))
    clearKeywordFilter()
})

function feedGoPage(p: number) {
  feedPage.value = Math.min(Math.max(1, p), feedTotalPages.value)
  fetchFeed()
}

async function refresh() {
  try {
    const [ov, kw, fd, nt] = await Promise.all([
      $fetch<Overview>('/api/account/overview'),
      $fetch<Keyword[]>('/api/account/keywords'),
      $fetch<PublicContentList>('/api/account/feed', { query: feedQuery.value }),
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
onMounted(() => {
  refresh()
  refreshCustomFeeds()
})
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
    notificationMessage.value = enabled ? '每日全站早报已启用。' : '邮件通知已关闭。'
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

function focusKeywordInput() {
  document.querySelector('#keyword-monitor')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.setTimeout(() => document.querySelector<HTMLInputElement>('#keyword-input')?.focus(), 350)
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

// QUAL-078：这两个原先只有 finally 没有 catch——请求失败时异常穿过 finally 变成
// unhandled rejection，页面表现是"什么都没发生"（按钮恢复、列表不动、零提示），
// 用户无法区分"停用成功了"和"停用失败了"。账号页没有管理端的 toast 挂载点，
// 复用上面加关键词时那个 keywordError 行内提示。
async function toggleKeyword(k: Keyword) {
  busy.value = true
  keywordError.value = ''
  try {
    await $fetch(`/api/account/keywords/${k.id}`, { method: 'PUT', body: { enabled: !k.enabled } })
    await refresh()
  } catch (exc: any) {
    keywordError.value = exc?.data?.detail ?? exc?.message ?? '操作失败，请稍后重试'
  } finally {
    busy.value = false
  }
}

async function removeKeyword(k: Keyword) {
  busy.value = true
  keywordError.value = ''
  try {
    await $fetch(`/api/account/keywords/${k.id}`, { method: 'DELETE' })
    await refresh()
  } catch (exc: any) {
    keywordError.value = exc?.data?.detail ?? exc?.message ?? '删除失败，请稍后重试'
  } finally {
    busy.value = false
  }
}

// ---------- 资料编辑 ----------
const editingProfile = ref(false)
const profileError = ref('')
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
    profileError.value = exc?.data?.detail ?? '保存失败'
  } finally {
    busy.value = false
  }
}

// ---------- 安全（低频操作，默认收起）----------
const showSecurity = ref(false)
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
          </div>
          <p class="mono mt-1 text-xs text-slate-400 dark:text-zinc-500">
            {{ user?.email }} · 注册于 {{ fmtTime(overview?.created_at) }} · 最近登录 {{ fmtTime(overview?.last_login_at) }}
          </p>
        </div>
      </div>

      <!-- 资料编辑 -->
      <div v-if="editingProfile" class="mt-5 rounded-xl border border-blue-500/30 bg-blue-500/5 p-4">
        <h3 class="text-sm font-semibold">编辑资料</h3>
        <label class="mt-3 block text-xs font-medium text-slate-500 dark:text-zinc-400">昵称（留空则使用邮箱前缀）</label>
        <input v-model="nameDraft" maxlength="24" placeholder="最多 24 个字符"
          class="input mt-1 w-full px-3 py-2 text-sm">
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
        <p v-if="profileError" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ profileError }}</p>
        <div class="mt-4 flex gap-2">
          <button class="btn-primary px-4 py-2" :disabled="busy" @click="saveProfile">保存</button>
          <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" @click="editingProfile = false">取消</button>
        </div>
      </div>
    </section>

    <div class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div class="space-y-5">
        <!-- 关键词监控 + 关注流（点击关键词筛选）-->
        <section id="keyword-monitor" class="panel scroll-mt-20 p-5">
          <div class="flex items-center justify-between gap-2">
            <h2 class="text-sm font-semibold">关键词监控</h2>
            <span class="text-xs text-slate-400">{{ overview?.keywords_enabled ?? 0 }} 个启用中<span v-if="overview?.keywords_enabled" class="ml-1 hidden sm:inline">· 点击关键词可筛选下方关注流</span></span>
          </div>
          <form class="mt-3 flex gap-2" @submit.prevent="addKeyword">
            <div class="relative flex-1">
              <Plus :size="14" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input id="keyword-input" v-model="newKeyword" placeholder="添加关键词，如 爱弥斯 / 菲比 / 3.6 版本" maxlength="64"
                class="input w-full py-2 pl-8 pr-3 text-sm">
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
              class="group flex items-center gap-1.5 rounded-full border py-1 pl-2 pr-1.5 text-sm transition-colors"
              :class="selectedKeywordId === k.id
                ? 'border-blue-600 bg-blue-600 text-white dark:border-blue-500 dark:bg-blue-500'
                : k.enabled
                  ? 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                  : 'border-slate-900/10 text-slate-400 dark:border-white/15 dark:text-zinc-500'"
            >
              <button :disabled="busy" :title="k.enabled ? '点击暂停' : '点击启用'" class="grid place-items-center" @click="toggleKeyword(k)">
                <span class="size-1.5 rounded-full" :class="k.enabled ? (selectedKeywordId === k.id ? 'bg-white' : 'bg-blue-500') : 'bg-slate-400'" />
              </button>
              <button
                :disabled="busy || !k.enabled"
                :title="k.enabled ? '点击筛选关注流' : '启用后可筛选'"
                class="flex items-center gap-1.5"
                @click="setKeywordFilter(k)"
              >
                <span :class="k.enabled ? '' : 'line-through'">{{ k.keyword }}</span>
                <span
                  v-if="k.enabled"
                  class="mono rounded-full px-1.5 text-[11px] leading-4 tabular-nums"
                  :class="selectedKeywordId === k.id ? 'bg-white/20' : 'bg-blue-500/15 dark:bg-blue-400/15'"
                >{{ k.matches }}</span>
              </button>
              <button :disabled="busy" class="opacity-40 transition-opacity group-hover:opacity-100" title="删除" @click="removeKeyword(k)">
                <X :size="13" />
              </button>
            </div>
          </div>
        </section>

        <!-- 定制监控（UID/关键词，可公开可推送） -->
        <section class="panel p-5">
          <h2 class="text-sm font-semibold">定制监控</h2>
          <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">
            自建 UID / 关键词抓取源；公开后出现在"定制化"频道，不公开仅自己可见。
          </p>
          <form class="mt-3 grid gap-2 sm:grid-cols-[7rem_1fr]" @submit.prevent="createCustomFeed">
            <select
              v-model="customForm.kind"
              class="select px-2.5 py-2 text-sm"
            >
              <option value="keyword">关键词监控</option>
              <option value="uid">UID 监控</option>
            </select>
            <input
              v-model="customForm.target_value"
              :placeholder="customForm.kind === 'uid' ? 'B 站 UID（纯数字）' : '监控关键词'"
              class="input px-3 py-2 text-sm"
            />
            <input
              v-model="customForm.name"
              placeholder="监控名称（如：某UP主动态 / 某话题）"
              class="input px-3 py-2 text-sm sm:col-span-2"
            />
            <input
              v-if="customForm.kind === 'keyword'"
              v-model.number="customForm.min_views"
              type="number"
              min="0"
              placeholder="播放量门槛（可选）"
              class="input px-3 py-2 text-sm"
            />
            <div class="flex flex-wrap items-center gap-4 sm:col-span-2">
              <label class="inline-flex cursor-pointer items-center gap-1.5 text-sm text-slate-600 dark:text-zinc-300">
                <input v-model="customForm.push_enabled" type="checkbox" class="checkbox" /> 新消息邮件推送
              </label>
              <label class="inline-flex cursor-pointer items-center gap-1.5 text-sm text-slate-600 dark:text-zinc-300">
                <input v-model="customForm.is_public" type="checkbox" class="checkbox" /> 公开到全站
              </label>
              <button type="submit" class="btn-primary ml-auto px-4 py-2 text-sm" :disabled="customBusy">创建监控</button>
            </div>
            <p v-if="customError" class="text-xs text-red-600 sm:col-span-2 dark:text-red-400">{{ customError }}</p>
          </form>

          <ul class="mt-3 space-y-1.5">
            <li
              v-for="f in customFeeds"
              :key="f.id"
              class="flex flex-wrap items-center gap-2 rounded-md border border-slate-900/5 px-3 py-2 text-sm dark:border-white/10"
              :class="!f.enabled || f.status !== 'active' ? 'opacity-60' : ''"
            >
              <span class="chip whitespace-nowrap bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300">
                {{ f.kind === 'uid' ? 'UID' : '关键词' }}
              </span>
              <span class="font-medium">{{ f.name }}</span>
              <span class="mono truncate text-xs text-slate-400">{{ f.target_value }}</span>
              <span class="mono text-xs text-slate-400">{{ f.item_count }} 条</span>
              <span
                class="chip whitespace-nowrap"
                :class="f.is_public
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                  : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
              >{{ f.is_public ? '公开' : '私有' }}</span>
              <button
                class="text-xs text-slate-500 underline-offset-2 hover:underline dark:text-zinc-400"
                @click="patchCustomFeed(f, { push_enabled: !f.push_enabled }, `推送已${!f.push_enabled ? '开启' : '关闭'}`)"
              >推送{{ f.push_enabled ? '开' : '关' }}</button>
              <NuxtLink v-if="f.is_public" :to="`/custom/${f.id}`" class="link text-xs">查看</NuxtLink>
              <button
                class="ml-auto text-xs text-red-500 hover:underline disabled:opacity-40"
                :disabled="customBusy"
                @click="removeCustomFeed(f)"
              >删除</button>
            </li>
            <li v-if="!customFeeds.length" class="text-xs text-slate-400 dark:text-zinc-500">
              还没有定制监控。创建一个 UID 或关键词监控，后台会每 30 分钟抓取一次。
            </li>
          </ul>
        </section>

        <!-- 我的商品跟踪（状态标记 + 到价提醒） -->
        <ProductTrackerPanel />

        <!-- 我的关注流 -->
        <section class="panel overflow-hidden">
          <div class="flex flex-wrap items-center justify-between gap-2 px-5 py-3.5">
            <div class="flex items-center gap-2">
              <h2 class="text-sm font-semibold">我的关注流</h2>
              <button
                v-if="selectedKeyword"
                type="button"
                class="inline-flex items-center gap-1 rounded-full bg-blue-600 px-2.5 py-1 text-xs font-medium text-white dark:bg-blue-500"
                title="清除筛选"
                @click="clearKeywordFilter"
              >
                {{ selectedKeyword.keyword }} <X :size="12" />
              </button>
            </div>
            <div class="flex items-center gap-2 text-xs text-slate-400 dark:text-zinc-500">
              <span v-if="(feed?.new_today ?? 0) > 0" class="rounded-full bg-emerald-500/10 px-2 py-0.5 font-medium text-emerald-600 dark:text-emerald-400">近24h +{{ feed?.new_today }}</span>
              <span>{{ feed?.total ?? 0 }} 条命中</span>
            </div>
          </div>
          <div v-if="feed?.items?.length" class="border-t border-slate-900/5 dark:border-white/5">
            <!-- 关注流跨分区：每条标注所属游戏分区 -->
            <RawFeedRow v-for="item in feed.items" :key="item.id" :item="toRawItem(item)" show-community />
          </div>
          <p v-else class="border-t border-slate-900/5 px-5 py-8 text-center text-sm text-slate-400 dark:border-white/5 dark:text-zinc-500">
            <template v-if="selectedKeyword">关键词「{{ selectedKeyword.keyword }}」暂无命中内容，系统持续采集中。</template>
            <template v-else-if="keywords.length">暂无命中关键词的内容，系统持续采集中。</template>
            <template v-else>添加关键词后，这里会展示命中的最新内容。</template>
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

      <!-- 右栏：通知 + 安全 -->
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
              :disabled="!!notificationBusy"
              @click="toggleNotifications"
            >
              <span class="absolute left-1 top-1 size-5 rounded-full bg-white shadow-sm transition-transform duration-200" :class="notificationInfo?.enabled ? 'translate-x-5' : 'translate-x-0'" />
            </button>
          </div>

          <div v-if="notificationBlockReason" class="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2.5 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
            <CircleAlert :size="14" class="mt-0.5 shrink-0" />
            <div class="min-w-0 flex-1">
              <p>{{ notificationBlockReason }}</p>
              <button
                v-if="notificationInfo?.enabled && !(overview?.keywords_enabled ?? 0)"
                type="button"
                class="mt-1 font-medium text-blue-700 underline underline-offset-2 hover:text-blue-800 dark:text-blue-300 dark:hover:text-blue-200"
                @click="focusKeywordInput"
              >
                去添加关键词
              </button>
            </div>
          </div>

          <div class="mt-4 grid gap-2">
            <button
              type="button"
              class="inline-flex min-h-11 w-full items-center justify-center gap-2 px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed"
              :class="notificationReady ? 'btn-primary' : 'rounded-md border border-slate-900/10 bg-slate-100 text-slate-400 disabled:opacity-100 dark:border-white/10 dark:bg-white/5 dark:text-zinc-500'"
              :disabled="!!notificationBusy || !notificationReady"
              @click="sendNotification('morning')"
            >
              <Mail :size="15" /> {{ notificationBusy === 'morning' ? '提交中…' : '发送今日全站早报' }}
            </button>
            <button
              type="button"
              class="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed"
              :class="aiNotificationReady ? 'border-slate-900/10 text-blue-700 hover:bg-blue-500/5 dark:border-white/15 dark:text-blue-300' : 'border-slate-900/10 bg-slate-50 text-slate-400 disabled:opacity-100 dark:border-white/10 dark:bg-white/[0.03] dark:text-zinc-500'"
              :disabled="!!notificationBusy || !aiNotificationReady"
              @click="sendNotification('ai')"
            >
              <Sparkles :size="15" /> {{ notificationBusy === 'ai' ? '提交中…' : '生成 AI 精炼通知' }}
            </button>
          </div>
          <p class="mt-2 flex items-start gap-1.5 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
            <Clock3 :size="13" class="mt-0.5 shrink-0" /> AI 会复核并总结最近 24 小时内容，通常需要几分钟，完成后自动发到当前邮箱。
          </p>
          <p
            v-if="notificationMessage"
            class="mt-3 text-xs"
            :class="notificationInfo?.enabled ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-zinc-400'"
          >{{ notificationMessage }}</p>
          <p v-if="notificationError" class="mt-3 text-xs text-red-600 dark:text-red-400">{{ notificationError }}</p>

          <div v-if="notificationInfo?.records.length" class="mt-4 border-t border-slate-900/5 pt-3 dark:border-white/5">
            <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">最近发送</p>
            <div class="mt-2 space-y-2">
              <div v-for="record in notificationInfo.records.slice(0, 3)" :key="record.id" class="rounded-lg bg-slate-500/5 px-3 py-2 dark:bg-white/5">
                <div class="flex items-center justify-between gap-2 text-xs">
                  <span class="font-medium">{{ record.kind === 'ai' ? 'AI 精炼通知' : '全站早报' }}</span>
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
          <div class="flex items-center justify-between gap-2">
            <h2 class="flex items-center gap-2 text-sm font-semibold"><KeyRound :size="15" /> 安全设置</h2>
            <button
              type="button"
              class="rounded-md border border-slate-900/10 px-3 py-1.5 text-xs text-slate-600 transition-colors hover:bg-slate-900/5 dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/10"
              @click="showSecurity = !showSecurity"
            >
              {{ showSecurity ? '收起' : '修改密码' }}
            </button>
          </div>
          <p v-if="!showSecurity" class="mt-1 text-xs text-slate-400 dark:text-zinc-500">修改密码后其他登录会话将全部退出。</p>
          <template v-else>
            <input v-model="oldPassword" type="password" autocomplete="current-password" placeholder="原密码"
              class="input mt-3 w-full px-3 py-2 text-sm">
            <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="新密码（至少 8 位）"
              class="input mt-2.5 w-full px-3 py-2 text-sm">
            <p v-if="securityErr" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ securityErr }}</p>
            <p v-if="securityMsg" class="mt-2 text-xs text-emerald-600 dark:text-emerald-400">{{ securityMsg }}</p>
            <button class="btn-primary mt-3 w-full px-4 py-2" :disabled="busy || !oldPassword || !newPassword" @click="changePassword">
              {{ busy ? '提交中…' : '修改密码' }}
            </button>
          </template>
        </section>
      </div>
    </div>
  </div>
</template>
