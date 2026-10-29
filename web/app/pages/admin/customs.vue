<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
import { customKindLabel } from '~/utils/custom-feeds'
import { Search } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '定制监控 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()

interface CustomFeedAdminRow {
  id: number
  name: string
  kind: string
  platform: string
  target_value: string
  min_views: number
  push_enabled: boolean
  is_public: boolean
  enabled: boolean
  status: string
  owner_email: string | null
  owner_name: string | null
  item_count: number
  created_at: string
  user_id: number | null
  source_id: number | null
  interval_minutes: number
}

interface BetaPolicy {
  open_beta: boolean
  allowed_user_ids: number[]
  user_overrides: Record<string, { max_feeds?: number, min_interval_minutes?: number }>
  max_feeds_per_user: number
  min_interval_minutes: number
  max_active_sources: number
  custom_hourly_budget: number
  platform_hourly_budget: number
}
interface CapacityInfo {
  policy: BetaPolicy
  capacity: { active_sources: number, estimated_tasks_per_hour: number }
  budget: { total: number, custom: number }
  usage_24h: { status: string, tasks: number, fetched_items: number, distributed_items: number }[]
  sources: { id: number, target_value: string, kind: string, next_run_at: string | null, consecutive_failures: number, last_error: string | null }[]
}
const capacityInfo = ref<CapacityInfo | null>(null)
const policyDraft = ref<BetaPolicy | null>(null)
const allowedUsers = ref('')
const policyBusy = ref(false)
const quotaUser = reactive({ id: '', max_feeds: 10, min_interval_minutes: 30 })
const policyFields = [
  ['max_feeds_per_user', '默认每人监控数'], ['min_interval_minutes', '新监控最短周期（分钟）'],
  ['max_active_sources', '最多独立目标'], ['custom_hourly_budget', '定制任务预算/小时'],
  ['platform_hourly_budget', 'B站全平台任务预算/小时'],
] as const

async function refreshCapacity() {
  const data = await api<CapacityInfo>('/api/admin/custom-feeds/capacity')
  capacityInfo.value = data
  policyDraft.value = structuredClone(data.policy)
  allowedUsers.value = data.policy.allowed_user_ids.join('，')
}
async function savePolicy(grantUser = false) {
  if (!policyDraft.value || policyBusy.value) return
  policyBusy.value = true
  try {
    const ids = allowedUsers.value.split(/[,，\s]+/).filter(Boolean).map(Number)
    if (ids.some(id => !Number.isInteger(id) || id < 1)) throw new Error('公测名额需填写有效用户 ID')
    const body = structuredClone(toRaw(policyDraft.value))
    body.allowed_user_ids = [...new Set(ids)]
    if (grantUser) {
      const id = Number(quotaUser.id)
      if (!Number.isInteger(id) || id < 1) throw new Error('请填写用户 ID')
      body.allowed_user_ids = [...new Set([...body.allowed_user_ids, id])]
      body.user_overrides[String(id)] = { max_feeds: quotaUser.max_feeds, min_interval_minutes: quotaUser.min_interval_minutes }
    }
    await api('/api/admin/custom-feeds/policy', { method: 'PUT', body })
    await refreshCapacity()
    toast.ok(grantUser ? '公测名额与个人配额已保存' : '公测配置已保存')
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '保存失败')
  } finally { policyBusy.value = false }
}

const items = ref<CustomFeedAdminRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const qInput = ref('')
const q = ref('')
const kindFilter = ref('')
const statusFilter = ref('')
const loading = ref(false)
const error = ref<string | null>(null)
const busyId = ref<number | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const query: Record<string, string | number> = { page: page.value, page_size: pageSize }
    if (q.value.trim()) query.q = q.value.trim()
    if (kindFilter.value) query.kind = kindFilter.value
    if (statusFilter.value) query.feed_status = statusFilter.value
    const data = await api<{ items: CustomFeedAdminRow[], total: number }>('/api/admin/custom-feeds', { query })
    items.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}
onMounted(() => Promise.all([refresh(), refreshCapacity().catch(exc => toast.err(`容量加载失败：${exc.message}`))]))
watch(page, refresh)

function applyFilters() {
  page.value = 1
  q.value = qInput.value
  refresh()
}
watch([kindFilter, statusFilter], () => {
  page.value = 1
  q.value = qInput.value
  refresh()
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function patchFeed(feed: CustomFeedAdminRow, body: Record<string, unknown>, okText: string) {
  if (busyId.value) return
  busyId.value = feed.id
  try {
    const updated = await api<CustomFeedAdminRow>(`/api/admin/custom-feeds/${feed.id}`, {
      method: 'PATCH',
      body,
    })
    Object.assign(feed, updated)
    toast.ok(okText)
  } catch (exc: any) {
    toast.err(`操作失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

async function collectNow(feed: CustomFeedAdminRow) {
  if (busyId.value) return
  busyId.value = feed.id
  try {
    await api(`/api/admin/custom-feeds/${feed.id}/collect`, { method: 'POST' })
    toast.ok(`「${feed.name}」已触发抓取`)
  } catch (exc: any) {
    toast.err(`触发失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

async function removeFeed(feed: CustomFeedAdminRow) {
  if (busyId.value) return
  if (!confirm(`彻底删除监控「${feed.name}」（${feed.owner_email}）及其全部抓取记录？`)) return
  busyId.value = feed.id
  try {
    await api(`/api/admin/custom-feeds/${feed.id}`, { method: 'DELETE' })
    items.value = items.value.filter((i) => i.id !== feed.id)
    toast.ok(`已删除「${feed.name}」`)
  } catch (exc: any) {
    toast.err(`删除失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="wall-heading">
      <h1 class="text-2xl font-bold text-ink">定制监控</h1>
      <p class="mt-0.5 text-sm text-muted">全站用户自建的 UID / 关键词监控源巡查与处置。</p>
    </div>

    <section class="panel space-y-4 p-4" aria-label="定制采集公测容量">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="font-semibold text-ink">公测名额与采集容量</h2>
        <button type="button" class="link text-xs" @click="refreshCapacity().catch(exc => toast.err(exc.message))">刷新容量</button>
      </div>
      <template v-if="capacityInfo && policyDraft">
        <div class="grid gap-2 text-sm text-muted sm:grid-cols-3">
          <p>独立目标 <strong class="mono text-ink">{{ capacityInfo.capacity.active_sources }}/{{ capacityInfo.policy.max_active_sources }}</strong></p>
          <p>预计 <strong class="mono text-ink">{{ capacityInfo.capacity.estimated_tasks_per_hour }}</strong> 任务/小时</p>
          <p>本小时定制 <strong class="mono text-ink">{{ capacityInfo.budget.custom }}/{{ capacityInfo.policy.custom_hourly_budget }}</strong> · 平台 {{ capacityInfo.budget.total }}/{{ capacityInfo.policy.platform_hourly_budget }}</p>
        </div>
        <p class="text-xs leading-relaxed text-faint">同平台、类型和目标共用一次采集，按订阅者最短周期估算。100 人各建 5 个不同目标、30 分钟一次，需要 1000 个任务/小时。任务预算含失败尝试，实际 HTTP 请求数可能更多。</p>
        <form class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="savePolicy()">
          <label v-for="[key,label] in policyFields" :key="key" class="text-xs text-muted">{{ label }}<input v-model.number="policyDraft[key]" type="number" required min="1" :max="key === 'min_interval_minutes' ? 1440 : key === 'max_feeds_per_user' ? 100 : 10000" class="input mt-1 w-full px-3 py-2 text-sm"></label>
          <label class="text-xs text-muted">公测用户 ID（逗号分隔）<input v-model="allowedUsers" class="input mt-1 w-full px-3 py-2 text-sm"></label>
          <div class="flex flex-wrap items-center gap-3 sm:col-span-2 lg:col-span-3">
            <label class="inline-flex items-center gap-2 text-sm text-muted"><input v-model="policyDraft.open_beta" type="checkbox" class="checkbox">向所有已验证用户开放</label>
            <button type="submit" class="btn-primary ml-auto px-4 py-2 text-sm" :disabled="policyBusy">保存公测配置</button>
          </div>
        </form>
        <form class="flex flex-wrap items-end gap-3 border-t border-line-soft pt-3" @submit.prevent="savePolicy(true)">
          <label class="text-xs text-muted">用户 ID<input v-model="quotaUser.id" type="number" min="1" required class="input mt-1 block w-28 px-3 py-2 text-sm"></label>
          <label class="text-xs text-muted">个人监控额度<input v-model.number="quotaUser.max_feeds" type="number" min="1" max="100" required class="input mt-1 block w-28 px-3 py-2 text-sm"></label>
          <label class="text-xs text-muted">最短周期（分钟）<input v-model.number="quotaUser.min_interval_minutes" type="number" min="5" max="1440" required class="input mt-1 block w-32 px-3 py-2 text-sm"></label>
          <button type="submit" class="btn px-3 py-2 text-sm" :disabled="policyBusy">保存个人配额并开通</button>
        </form>
        <p class="text-xs text-faint">额度与周期配置仅约束新增或加速操作，不修改已有监控。邮件已接入共用通知输入；AstrBot 定制推送待完成用户与会话的授权绑定。</p>
        <div class="flex flex-wrap gap-3 text-xs text-muted">
          <span v-for="row in capacityInfo.usage_24h" :key="row.status">近 24 小时 {{ row.status }}：{{ row.tasks }} 个任务，取得 {{ row.fetched_items }} 条，分发 {{ row.distributed_items }} 条</span>
        </div>
        <details v-if="capacityInfo.sources.length" class="text-xs text-muted">
          <summary class="cursor-pointer">共享目标运行状态</summary>
          <ul class="mt-2 space-y-2">
            <li v-for="source in capacityInfo.sources" :key="source.id" class="break-words">#{{ source.id }} {{ source.kind }} · {{ source.target_value }} · 下次 {{ fmtTime(source.next_run_at) }}<span v-if="source.last_error" class="ml-2 text-amber-700 dark:text-amber-300">连续失败 {{ source.consecutive_failures }} · {{ source.last_error }}</span></li>
          </ul>
        </details>
      </template>
      <p v-else class="text-xs text-muted">正在读取公测配置…</p>
    </section>

    <section class="panel p-4">
      <div class="flex flex-wrap items-center gap-2">
        <form class="flex items-center gap-2" @submit.prevent="applyFilters">
          <input
            v-model="qInput"
            placeholder="监控名 / 目标 / 用户邮箱"
            class="input w-56 px-3 py-2 text-sm"
          />
          <button type="submit" class="btn-primary px-4 py-2 text-sm" :disabled="loading">
            <Search :size="14" class="mr-1 inline" /> 搜索
          </button>
        </form>
        <select
          v-model="kindFilter"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部类型</option>
          <option value="uid">UID 监控</option>
          <option value="keyword">关键词监控</option>
          <option value="notices">官方公告</option>
          <option value="channel_rss">官方视频</option>
          <option value="account_dynamics">官方动态</option>
        </select>
        <select
          v-model="statusFilter"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部处置状态</option>
          <option value="active">正常</option>
          <option value="disabled">已停用</option>
        </select>
        <span class="ml-auto text-xs text-faint">共 {{ total }} 个监控</span>
      </div>

      <p v-if="error" class="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <div class="mt-3 overflow-x-auto">
        <table class="w-full min-w-3xl text-sm">
          <thead>
            <tr class="border-b border-line-soft text-left text-xs text-muted dark:border-line">
              <th class="px-3 py-2.5 font-medium">监控</th>
              <th class="px-3 py-2.5 font-medium">类型</th>
              <th class="px-3 py-2.5 font-medium">所有者</th>
              <th class="px-3 py-2.5 font-medium">条目</th>
              <th class="px-3 py-2.5 font-medium">公开</th>
              <th class="px-3 py-2.5 font-medium">推送</th>
              <th class="px-3 py-2.5 font-medium">处置</th>
              <th class="px-3 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="f in items"
              :key="f.id"
              class="border-b border-line-soft last:border-0"
              :class="f.status === 'disabled' || !f.enabled ? 'opacity-60' : ''"
            >
              <td class="px-3 py-2.5">
                <span class="font-medium">{{ f.name }}</span>
                <span class="mono ml-1.5 text-xs text-faint">{{ f.target_value }}</span>
                <span class="ml-1 text-xs text-faint">{{ f.interval_minutes }} 分钟/次 · 共享源 #{{ f.source_id || '待绑定' }}</span>
                <span v-if="f.min_views > 0" class="mono ml-1 text-[11px] text-faint">≥{{ f.min_views }} 播放</span>
              </td>
              <td class="px-3 py-2.5 text-muted">{{ customKindLabel(f.kind) }}</td>
              <td class="px-3 py-2.5">
                <span class="text-ink-2">{{ f.owner_email || '站点维护' }}</span>
                <span v-if="f.user_id" class="ml-1 text-xs text-faint">用户 #{{ f.user_id }}</span>
                <span v-if="f.owner_name" class="ml-1 text-xs text-faint">{{ f.owner_name }}</span>
              </td>
              <td class="mono px-3 py-2.5 text-muted">{{ f.item_count }}</td>
              <td class="px-3 py-2.5">
                <button
                  class="min-h-9 cursor-pointer rounded px-2 text-xs underline-offset-2 hover:underline disabled:cursor-wait"
                  :class="f.is_public ? 'text-emerald-700 dark:text-emerald-300' : 'text-faint'"
                  :disabled="busyId === f.id"
                  @click="patchFeed(f, { is_public: !f.is_public }, `已${f.is_public ? '取消' : '设为'}公开`)"
                >{{ f.is_public ? '公开' : '私有' }}</button>
              </td>
              <td class="px-3 py-2.5 text-muted">{{ f.push_enabled ? '开' : '关' }}</td>
              <td class="px-3 py-2.5">
                <button
                  class="min-h-9 cursor-pointer rounded px-2 text-xs underline-offset-2 hover:underline disabled:cursor-wait"
                  :class="f.status === 'disabled' ? 'text-red-500 dark:text-red-400' : 'text-emerald-700 dark:text-emerald-300'"
                  :disabled="busyId === f.id"
                  @click="patchFeed(f, { status: f.status === 'disabled' ? 'active' : 'disabled' }, f.status === 'disabled' ? '已恢复' : '已停用')"
                >{{ f.status === 'disabled' ? '已停用' : '正常' }}</button>
              </td>
              <td class="whitespace-nowrap px-3 py-2.5 text-right">
                <button class="min-h-9 rounded px-2 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === f.id" @click="collectNow(f)">抓取</button>
                <NuxtLink v-if="f.is_public" :to="`/custom/${f.id}`" target="_blank" class="min-h-9 inline-block rounded px-2 text-xs text-muted hover:underline">查看</NuxtLink>
                <button class="min-h-9 rounded px-2 text-xs text-red-500 hover:underline disabled:cursor-wait" :disabled="busyId === f.id" @click="removeFeed(f)">删除</button>
              </td>
            </tr>
            <tr v-if="!items.length && !loading">
              <td colspan="8" class="px-3 py-10 text-center text-sm text-faint">没有匹配的定制监控</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-line-soft px-3 py-2.5 text-xs text-faint dark:border-line">
        <span>共 {{ total }} 个监控</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-ink-2" @click="page--">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-ink-2" @click="page++">下一页 »</button>
        </div>
      </div>
    </section>
  </div>
</template>
