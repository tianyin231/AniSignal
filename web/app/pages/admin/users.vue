<script setup lang="ts">
import type { AdminDelivery, AdminUser, AdminUserDetail, AdminUserList } from '~/utils/admin-types'
import { fmtTime } from '~/utils/admin-types'
import { Ban, Mail, RotateCw, Search, Send } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '用户管理 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()

/* ---------- 用户快查（万级规模：服务端分页 + 显式查询） ---------- */
const items = ref<AdminUser[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const qInput = ref('')
const q = ref('')
const statusFilter = ref('')
const verifiedFilter = ref('')
const loading = ref(false)
const error = ref<string | null>(null)
const busyId = ref<number | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const query: Record<string, string | number> = { page: page.value, page_size: pageSize }
    if (q.value.trim()) query.q = q.value.trim()
    if (statusFilter.value) query.user_status = statusFilter.value
    if (verifiedFilter.value) query.verified = verifiedFilter.value
    const data = await api<AdminUserList>('/api/admin/users', { query })
    items.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}
onMounted(refresh)
watch(page, refresh)

function applyFilters() {
  page.value = 1
  q.value = qInput.value
  refresh()
}
watch([statusFilter, verifiedFilter], () => {
  page.value = 1
  q.value = qInput.value
  refresh()
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

/* ---------- 详情（关键词订阅 / 邮件记录） ---------- */
const detail = ref<AdminUserDetail | null>(null)
const detailLoading = ref(false)

async function openDetail(user: AdminUser) {
  if (detail.value?.id === user.id) {
    detail.value = null
    return
  }
  detailLoading.value = true
  try {
    detail.value = await api<AdminUserDetail>(`/api/admin/users/${user.id}`)
  } catch (exc: any) {
    toast.err(`详情加载失败：${exc?.message ?? exc}`)
  } finally {
    detailLoading.value = false
  }
}

/* ---------- 封禁 / 解封 / 订阅开关 ---------- */
async function setBanned(user: AdminUser, banned: boolean) {
  if (busyId.value) return
  let reason = ''
  if (banned) {
    reason = prompt(`封禁 ${user.email}：填写封禁原因（用户可见于管理端，可留空）`) ?? ''
    if (reason === null) return
    if (!confirm(`确认封禁 ${user.email}？将立即踢出其全部登录会话。`)) return
  } else if (!confirm(`确认解除 ${user.email} 的封禁？`)) {
    return
  }
  busyId.value = user.id
  try {
    const body: Record<string, unknown> = { banned }
    if (banned) body.ban_reason = reason
    const updated = await api<AdminUser>(`/api/admin/users/${user.id}`, { method: 'PATCH', body })
    Object.assign(user, updated)
    toast.ok(banned ? `已封禁 ${user.email}` : `已解封 ${user.email}`)
  } catch (exc: any) {
    toast.err(`操作失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

async function toggleNotification(user: AdminUser) {
  if (busyId.value) return
  busyId.value = user.id
  try {
    const updated = await api<AdminUser>(`/api/admin/users/${user.id}`, {
      method: 'PATCH',
      body: { notification_enabled: !user.notification_enabled },
    })
    Object.assign(user, updated)
    toast.ok(`${user.email} 通知订阅已${updated.notification_enabled ? '开启' : '关闭'}`)
  } catch (exc: any) {
    toast.err(`操作失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

/* ---------- 单发邮件 ---------- */
const mailOpen = ref(false)
const mailTarget = ref<AdminUser | null>(null)
const mailSubject = ref('')
const mailBody = ref('')
const mailBusy = ref(false)

function openMail(user: AdminUser) {
  mailTarget.value = user
  mailOpen.value = true
  mailSubject.value = ''
  mailBody.value = ''
}

async function sendMail() {
  if (!mailTarget.value || mailBusy.value) return
  if (!mailSubject.value.trim() || !mailBody.value.trim()) {
    toast.err('主题与正文不能为空')
    return
  }
  if (!confirm(`向 ${mailTarget.value.email} 发送「${mailSubject.value}」？`)) return
  mailBusy.value = true
  try {
    await api(`/api/admin/users/${mailTarget.value.id}/email`, {
      method: 'POST',
      body: { subject: mailSubject.value, body: mailBody.value },
    })
    toast.ok(`已进入发送队列：${mailTarget.value.email}`)
    mailOpen.value = false
    await refreshDeliveries()
    if (detail.value?.id === mailTarget.value.id) await openDetail(mailTarget.value)
  } catch (exc: any) {
    toast.err(`发送失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    mailBusy.value = false
  }
}

/* ---------- 广播邮件 ---------- */
const broadcast = reactive({ subject: '', body: '', verified_only: true })
const broadcastBusy = ref(false)

async function sendBroadcast() {
  if (broadcastBusy.value) return
  if (!broadcast.subject.trim() || !broadcast.body.trim()) {
    toast.err('主题与正文不能为空')
    return
  }
  if (!confirm(`向全部目标用户广播「${broadcast.subject}」？每位目标用户各发一封，批量入队后由后台分批投递。`)) return
  broadcastBusy.value = true
  try {
    const res = await api<{ targets: number, queued: number }>('/api/admin/users/broadcast', {
      method: 'POST',
      body: {
        subject: broadcast.subject,
        body: broadcast.body,
        verified_only: broadcast.verified_only,
        confirm: true,
      },
    })
    toast.ok(`广播已入队：${res.queued}/${res.targets} 封，后台分批投递`)
    broadcast.subject = ''
    broadcast.body = ''
    await refreshDeliveries()
  } catch (exc: any) {
    toast.err(`广播失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    broadcastBusy.value = false
  }
}

/* ---------- 触达记录 ---------- */
const deliveries = ref<AdminDelivery[]>([])
const deliveriesLoading = ref(false)

async function refreshDeliveries() {
  deliveriesLoading.value = true
  try {
    const data = await api<{ items: AdminDelivery[] }>('/api/admin/users/deliveries/recent', {
      query: { limit: 30 },
    })
    deliveries.value = data.items
  } catch { /* 记录拉取失败不打断页面 */ } finally {
    deliveriesLoading.value = false
  }
}
onMounted(refreshDeliveries)

const kindLabels: Record<string, string> = { admin: '单发', broadcast: '广播', morning: '早报', ai: 'AI' }
const deliveryStatusLabel: Record<string, string> = {
  pending: '待发送',
  sent: '已发送',
  failed: '失败',
  skipped: '跳过',
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">用户管理</h1>
      <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">快查、封禁、订阅管理与邮件触达。</p>
    </div>

    <!-- 广播邮件 -->
    <section class="panel p-4">
      <h2 class="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-zinc-100">
        <Send :size="14" /> 广播邮件
      </h2>
      <div class="mt-3 grid gap-3">
        <input
          v-model="broadcast.subject"
          placeholder="邮件主题（不超过 200 字）"
          class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
        />
        <textarea
          v-model="broadcast.body"
          rows="4"
          placeholder="邮件正文（纯文本，将自动套用邮件模板）"
          class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
        ></textarea>
        <div class="flex flex-wrap items-center gap-4">
          <label class="inline-flex cursor-pointer items-center gap-1.5 text-sm text-slate-600 dark:text-zinc-300">
            <input v-model="broadcast.verified_only" type="checkbox" class="size-4 accent-blue-600" />
            仅发给已验证用户
          </label>
          <span class="text-xs text-slate-400 dark:text-zinc-500">封禁用户始终排除；按 50 人/批后台分批投递</span>
          <button
            class="btn-primary ml-auto px-4 py-2 text-sm"
            :disabled="broadcastBusy"
            @click="sendBroadcast"
          >
            {{ broadcastBusy ? '入队中…' : '发送广播' }}
          </button>
        </div>
      </div>
    </section>

    <!-- 用户快查 -->
    <section class="panel p-4">
      <div class="flex flex-wrap items-center gap-2">
        <form class="flex items-center gap-2" @submit.prevent="applyFilters">
          <input
            v-model="qInput"
            placeholder="邮箱 / 昵称"
            class="w-56 rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
          />
          <button type="submit" class="btn-primary px-4 py-2 text-sm" :disabled="loading">
            <Search :size="14" class="mr-1 inline" /> 搜索
          </button>
        </form>
        <select
          v-model="statusFilter"
          class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
        >
          <option value="">全部状态</option>
          <option value="active">正常</option>
          <option value="banned">已封禁</option>
        </select>
        <select
          v-model="verifiedFilter"
          class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
        >
          <option value="">全部验证状态</option>
          <option value="yes">已验证</option>
          <option value="no">未验证</option>
        </select>
        <span class="ml-auto text-xs text-slate-400 dark:text-zinc-500">共 {{ total }} 位用户</span>
      </div>

      <p v-if="error" class="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <div class="mt-3 overflow-x-auto">
        <table class="w-full min-w-3xl text-sm">
          <thead>
            <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
              <th class="px-3 py-2.5 font-medium">用户</th>
              <th class="px-3 py-2.5 font-medium">状态</th>
              <th class="px-3 py-2.5 font-medium">验证</th>
              <th class="px-3 py-2.5 font-medium">通知订阅</th>
              <th class="px-3 py-2.5 font-medium">关键词</th>
              <th class="px-3 py-2.5 font-medium">注册</th>
              <th class="px-3 py-2.5 font-medium">最近登录</th>
              <th class="px-3 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="u in items"
              :key="u.id"
              class="border-b border-slate-900/5 last:border-0 dark:border-white/5"
              :class="u.status === 'banned' ? 'opacity-60' : ''"
            >
              <td class="px-3 py-2.5">
                <span class="font-medium">{{ u.email }}</span>
                <span v-if="u.display_name" class="ml-1.5 text-xs text-slate-400">{{ u.display_name }}</span>
              </td>
              <td class="px-3 py-2.5">
                <span
                  class="chip whitespace-nowrap"
                  :class="u.status === 'banned'
                    ? 'bg-red-500/10 text-red-600 dark:text-red-400'
                    : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'"
                >{{ u.status === 'banned' ? '已封禁' : '正常' }}</span>
              </td>
              <td class="px-3 py-2.5 text-slate-500 dark:text-zinc-400">{{ u.is_verified ? '已验证' : '未验证' }}</td>
              <td class="px-3 py-2.5">
                <button
                  class="min-h-9 cursor-pointer rounded px-2 text-xs underline-offset-2 hover:underline disabled:cursor-wait"
                  :class="u.notification_enabled ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-400'"
                  :disabled="busyId === u.id"
                  @click="toggleNotification(u)"
                >
                  {{ u.notification_enabled ? '已订阅' : '未订阅' }}
                </button>
              </td>
              <td class="mono px-3 py-2.5 text-slate-500 dark:text-zinc-400">{{ u.keyword_count ?? 0 }}</td>
              <td class="mono px-3 py-2.5 text-xs text-slate-400">{{ fmtTime(u.created_at) }}</td>
              <td class="mono px-3 py-2.5 text-xs text-slate-400">{{ u.last_login_at ? fmtTime(u.last_login_at) : '—' }}</td>
              <td class="whitespace-nowrap px-3 py-2.5 text-right">
                <button class="min-h-9 rounded px-2 text-xs text-blue-600 hover:underline dark:text-blue-300" @click="openDetail(u)">详情</button>
                <button class="min-h-9 rounded px-2 text-xs text-slate-600 hover:underline dark:text-zinc-300" @click="openMail(u)">
                  <Mail :size="12" class="mr-0.5 inline" />邮件
                </button>
                <button
                  class="min-h-9 rounded px-2 text-xs hover:underline disabled:cursor-wait"
                  :class="u.status === 'banned' ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-500 dark:text-red-400'"
                  :disabled="busyId === u.id"
                  @click="setBanned(u, u.status !== 'banned')"
                >
                  <Ban :size="12" class="mr-0.5 inline" />{{ u.status === 'banned' ? '解封' : '封禁' }}
                </button>
              </td>
            </tr>
            <tr v-if="!items.length && !loading">
              <td colspan="8" class="px-3 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">没有匹配的用户</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-900/5 px-3 py-2.5 text-xs text-slate-400 dark:border-white/10 dark:text-zinc-500">
        <span>共 {{ total }} 位用户</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="page--">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="page++">下一页 »</button>
        </div>
      </div>
    </section>

    <!-- 用户详情 -->
    <section v-if="detail" class="panel p-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">
          {{ detail.email }}
          <span v-if="detail.display_name" class="ml-1 text-xs font-normal text-slate-400">{{ detail.display_name }}</span>
        </h2>
        <button class="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-zinc-300" @click="detail = null">收起</button>
      </div>
      <p v-if="detail.status === 'banned'" class="mt-2 rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
        已封禁{{ detail.banned_at ? `（${fmtTime(detail.banned_at)}）` : '' }}{{ detail.banned_reason ? `：${detail.banned_reason}` : '' }}
      </p>

      <div class="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <h3 class="text-xs font-medium text-slate-400 dark:text-zinc-500">关键词订阅（{{ detail.keywords.length }}）</h3>
          <div class="mt-2 flex flex-wrap gap-1.5">
            <span
              v-for="k in detail.keywords"
              :key="k.id"
              class="rounded px-2 py-0.5 text-[11px]"
              :class="k.enabled
                ? 'bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300'
                : 'bg-slate-500/10 text-slate-400 line-through dark:text-zinc-500'"
            >{{ k.keyword }}</span>
            <span v-if="!detail.keywords.length" class="text-xs text-slate-400">未设置关键词</span>
          </div>
          <p class="mt-3 text-xs text-slate-400 dark:text-zinc-500">累计送达邮件 {{ detail.sent_total }} 封</p>
        </div>
        <div>
          <h3 class="text-xs font-medium text-slate-400 dark:text-zinc-500">最近投递记录</h3>
          <ul class="mt-2 space-y-1 text-xs">
            <li v-for="d in detail.recent_deliveries" :key="d.id" class="flex items-center gap-2">
              <span class="mono text-slate-400">{{ fmtTime(d.created_at) }}</span>
              <span class="rounded bg-slate-500/10 px-1.5 py-0.5 text-[10px] text-slate-500 dark:text-zinc-400">{{ kindLabels[d.kind] ?? d.kind }}</span>
              <span class="min-w-0 flex-1 truncate">{{ d.subject || '—' }}</span>
              <span :class="d.status === 'sent' ? 'text-emerald-600 dark:text-emerald-400' : d.status === 'failed' ? 'text-red-500' : 'text-slate-400'">{{ deliveryStatusLabel[d.status] ?? d.status }}</span>
            </li>
            <li v-if="!detail.recent_deliveries.length" class="text-slate-400">暂无投递记录</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 单发邮件 -->
    <section v-if="mailOpen && mailTarget" class="panel p-4">
      <h2 class="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-zinc-100">
        <Mail :size="14" /> 发送邮件 → {{ mailTarget.email }}
      </h2>
      <div class="mt-3 grid gap-3">
        <input
          v-model="mailSubject"
          placeholder="邮件主题（如：违规提醒 / 专属优惠）"
          class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
        />
        <textarea
          v-model="mailBody"
          rows="4"
          placeholder="邮件正文（纯文本，将自动套用邮件模板）"
          class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
        ></textarea>
        <div class="flex items-center justify-end gap-2">
          <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm text-slate-600 dark:border-white/15 dark:text-zinc-300" @click="mailOpen = false">取消</button>
          <button class="btn-primary px-4 py-2 text-sm" :disabled="mailBusy" @click="sendMail">
            <Send :size="13" class="mr-1 inline" />{{ mailBusy ? '入队中…' : '发送' }}
          </button>
        </div>
      </div>
    </section>

    <!-- 触达记录 -->
    <section class="panel p-4">
      <h2 class="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-zinc-100">
        <RotateCw v-if="deliveriesLoading" :size="14" class="animate-spin" /> 触达记录（单发 / 广播）
      </h2>
      <ul class="mt-3 space-y-1 text-xs">
        <li v-for="d in deliveries" :key="d.id" class="flex flex-wrap items-center gap-2">
          <span class="mono text-slate-400">{{ fmtTime(d.created_at) }}</span>
          <span class="rounded bg-slate-500/10 px-1.5 py-0.5 text-[10px] text-slate-500 dark:text-zinc-400">{{ kindLabels[d.kind] ?? d.kind }}</span>
          <span class="text-slate-600 dark:text-zinc-300">{{ d.email }}</span>
          <span class="min-w-0 flex-1 truncate">{{ d.subject || '—' }}</span>
          <span :class="d.status === 'sent' ? 'text-emerald-600 dark:text-emerald-400' : d.status === 'failed' ? 'text-red-500' : 'text-slate-400'">{{ deliveryStatusLabel[d.status] ?? d.status }}</span>
        </li>
        <li v-if="!deliveries.length && !deliveriesLoading" class="text-slate-400 dark:text-zinc-500">暂无管理端邮件记录</li>
      </ul>
    </section>
  </div>
</template>
