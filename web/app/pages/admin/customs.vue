<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
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
  owner_email: string
  owner_name: string | null
  item_count: number
  created_at: string
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
onMounted(refresh)
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
                <span v-if="f.min_views > 0" class="mono ml-1 text-[11px] text-faint">≥{{ f.min_views }} 播放</span>
              </td>
              <td class="px-3 py-2.5 text-muted">{{ f.kind === 'uid' ? 'UID' : '关键词' }}</td>
              <td class="px-3 py-2.5">
                <span class="text-ink-2">{{ f.owner_email }}</span>
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
