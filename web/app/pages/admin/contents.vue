<script setup lang="ts">
import type { ContentList, ContentItem } from '~/utils/admin-types'
import { contentTypeLabels, fmtTime, platformLabels } from '~/utils/admin-types'
import { ADMIN_COMMUNITIES, ADMIN_COMMUNITY_BADGE, ADMIN_COMMUNITY_SHORT } from '~/utils/admin-communities'
import { Pencil, RotateCw, Trash2 } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '内容浏览 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()
const items = ref<ContentItem[]>([])
const total = ref(0)
const platform = ref<string>('')
const communityFilter = ref<string>('')
const reviewFilter = ref<string>('')
const tierFilter = ref<string>('')
const hiddenOnly = ref(false)
const q = ref('')
const page = ref(1)
const pageSize = 50
const error = ref<string | null>(null)
const loading = ref(false)
const busyId = ref<number | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const query: Record<string, string | number | boolean> = { limit: pageSize, offset: (page.value - 1) * pageSize }
    if (platform.value) query.platform = platform.value
    if (communityFilter.value) query.community = communityFilter.value
    if (reviewFilter.value) query.review = reviewFilter.value
    if (hiddenOnly.value) query.hidden = true
    if (tierFilter.value) query.tier = tierFilter.value
    if (q.value.trim()) query.q = q.value.trim()
    const data = await api<ContentList>('/api/admin/contents', { query })
    items.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  refresh()
}

onMounted(refresh)
// 翻页立即生效；筛选条件变更后需点击「查询」应用，避免无效查询压力
watch([platform, communityFilter, reviewFilter, hiddenOnly, tierFilter], () => {
  page.value = 1
})
watch(page, refresh)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const metricText = (metrics: Record<string, number> | null) => {
  if (!metrics) return '—'
  const parts: string[] = []
  if (metrics.view !== undefined) parts.push(`播 ${metrics.view}`)
  if (metrics.like !== undefined) parts.push(`赞 ${metrics.like}`)
  if (metrics.reply !== undefined) parts.push(`评 ${metrics.reply}`)
  return parts.join(' · ') || '—'
}

const rereviewBusy = ref(false)
async function rereviewCommunity() {
  if (!communityFilter.value || rereviewBusy.value) return
  const label = ADMIN_COMMUNITIES.find(c => c.key === communityFilter.value)?.label ?? communityFilter.value
  if (!confirm(`重审「${label}」分区的全部内容？将清除既有 AI 判定并重新审核（下架状态会按新判定更新）。`)) return
  rereviewBusy.value = true
  try {
    const res = await api<{ cleared: number, queued: number }>('/api/admin/ai/review-community', {
      method: 'POST',
      query: { community: communityFilter.value },
    })
    toast.ok(`已入队重审 ${res.queued} 条（清除 ${res.cleared} 条旧判定），稍后刷新查看结果`)
    await refresh()
  } catch (exc: any) {
    toast.err(`重审失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    rereviewBusy.value = false
  }
}

function reviewBadge(item: ContentItem): { label: string; class: string } | null {
  const review = item.review
  if (!review) return null
  if (review.violation) return { label: '违规', class: 'bg-red-500/15 text-red-700 dark:text-red-300' }
  if (!review.relevant) return { label: '不相关', class: 'bg-amber-500/15 text-amber-700 dark:text-amber-300' }
  return { label: `AI ${review.quality_score}`, class: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' }
}

async function toggleHidden(item: ContentItem) {
  if (busyId.value) return
  busyId.value = item.id
  try {
    const data = await api<{ hidden: boolean }>(`/api/admin/contents/${item.id}/visibility`, {
      method: 'PATCH',
      body: { hidden: !item.hidden },
    })
    item.hidden = data.hidden
    toast.ok(data.hidden ? `已下架 #${item.id}` : `已恢复展示 #${item.id}`)
  } catch (exc: any) {
    toast.err(`操作失败：${exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

async function deleteContent(item: ContentItem) {
  if (busyId.value) return
  if (!window.confirm(`确定彻底删除 #${item.id}「${item.title || item.external_id}」？该操作不可恢复。`)) return
  busyId.value = item.id
  try {
    await api(`/api/admin/contents/${item.id}`, { method: 'DELETE' })
    toast.ok(`已删除 #${item.id}`)
    await refresh()
  } catch (exc: any) {
    toast.err(`删除失败：${exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

// 编辑：产生新版本，不改写历史
const editing = ref<ContentItem | null>(null)
const editTitle = ref('')
const editText = ref('')
const editSaving = ref(false)

async function openEdit(item: ContentItem) {
  if (busyId.value) return
  busyId.value = item.id
  try {
    const detail = await api<{ title: string | null; text: string | null }>(`/api/admin/contents/${item.id}/detail`)
    editing.value = item
    editTitle.value = detail.title ?? ''
    editText.value = detail.text ?? ''
  } catch (exc: any) {
    toast.push(`读取失败：${exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}

async function saveEdit() {
  if (!editing.value || editSaving.value) return
  editSaving.value = true
  try {
    const data = await api<{ version: number }>(`/api/admin/contents/${editing.value.id}`, {
      method: 'PATCH',
      body: { title: editTitle.value, text: editText.value },
    })
    toast.push(`#${editing.value.id} 已修订为 v${data.version}`)
    editing.value = null
    await refresh()
  } catch (exc: any) {
    toast.push(`保存失败：${exc?.message ?? exc}`)
  } finally {
    editSaving.value = false
  }
}

async function rereview(item: ContentItem) {
  if (busyId.value) return
  busyId.value = item.id
  try {
    await api(`/api/admin/contents/${item.id}/review`, { method: 'POST' })
    toast.push(`#${item.id} 已加入重审队列`)
  } catch (exc: any) {
    toast.push(`提交失败：${exc?.message ?? exc}`)
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold">内容管理</h1>
        <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
          审核状态一目了然；支持下架/恢复、修订（产生新版本）、删除与重审。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="q"
          placeholder="标题关键词"
          class="input w-44 px-3 py-2 text-sm"
          @keyup.enter="page = 1; refresh()"
        >
        <select
          v-model="tierFilter"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部分级</option>
          <option value="core">核心来源</option>
          <option value="general">泛关键词</option>
          <option value="none">无匹配词</option>
        </select>
        <select
          v-model="communityFilter"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部分区</option>
          <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
        <select
          v-model="reviewFilter"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部审核结果</option>
          <option value="violation">违规</option>
          <option value="irrelevant">不相关</option>
          <option value="ok">正常</option>
        </select>
        <select
          v-model="platform"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部平台</option>
          <option v-for="(label, value) in platformLabels" :key="value" :value="value">{{ label }}</option>
        </select>
        <label class="inline-flex cursor-pointer items-center gap-1.5 text-sm text-slate-600 dark:text-zinc-300">
          <input v-model="hiddenOnly" type="checkbox" class="checkbox">
          仅看已下架
        </label>
        <button class="btn-primary px-4 py-2 text-sm" :disabled="loading" @click="applyFilters">
          {{ loading ? '查询中…' : '查询' }}
        </button>
        <button
          v-if="communityFilter"
          class="cursor-pointer rounded-md border border-blue-500/40 px-4 py-2 text-sm text-blue-600 transition-colors hover:bg-blue-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-blue-300"
          :disabled="rereviewBusy || loading"
          title="清除该分区全部 AI 判定并重新审核"
          @click="rereviewCommunity"
        >
          {{ rereviewBusy ? '重审入队中…' : '重审本分区' }}
        </button>
      </div>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-3xl text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">标题</th>
            <th class="px-4 py-2.5 font-medium">平台</th>
            <th class="px-4 py-2.5 font-medium">来源分级</th>
            <th class="px-4 py-2.5 font-medium">审核</th>
            <th class="px-4 py-2.5 font-medium">状态</th>
            <th class="px-4 py-2.5 font-medium">采集</th>
            <th class="px-4 py-2.5 font-medium">指标</th>
            <th class="px-4 py-2.5 font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5" :class="item.hidden ? 'opacity-60' : ''">
            <td class="max-w-72 px-4 py-2.5">
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="link block truncate font-medium">{{ item.title || item.external_id }}</a>
              <span v-else class="block truncate">{{ item.title || item.external_id }}</span>
              <span class="mono text-xs text-slate-400">v{{ item.version ?? '?' }} · {{ item.external_id }}</span>
              <p v-if="item.review?.reason" class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500" :title="item.review.reason">AI：{{ item.review.reason }}</p>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5 text-slate-600 dark:text-zinc-300">
              {{ platformLabels[item.platform] ?? item.platform }}
              <span class="chip ml-1 whitespace-nowrap" :class="ADMIN_COMMUNITY_BADGE[item.community] ?? 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'">{{ ADMIN_COMMUNITY_SHORT[item.community] ?? '鸣潮' }}</span>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5">
              <span
                class="chip"
                :class="item.tier === 'core' ? 'bg-blue-500/10 text-blue-700 dark:text-blue-300' : item.tier === 'general' ? 'bg-slate-500/10 text-slate-500 dark:text-zinc-400' : 'bg-zinc-500/10 text-zinc-500 dark:text-zinc-400'"
                :title="(item.matched_keywords || []).join('、')"
              >{{ item.tier === 'core' ? '核心来源' : item.tier === 'general' ? '泛关键词' : '无匹配' }}</span>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5">
              <span
                v-if="reviewBadge(item)"
                class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                :class="reviewBadge(item)!.class"
                :title="item.review?.reason"
              >{{ reviewBadge(item)!.label }}</span>
              <span v-else class="text-xs text-slate-400">未审核</span>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5">
              <span
                class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                :class="item.hidden ? 'bg-slate-500/15 text-slate-600 dark:text-zinc-300' : 'bg-blue-500/10 text-blue-700 dark:text-blue-300'"
              >{{ item.hidden ? '已下架' : '展示中' }}</span>
            </td>
            <td class="mono whitespace-nowrap px-4 py-2.5 text-slate-500 dark:text-zinc-400">{{ fmtTime(item.collected_at) }}</td>
            <td class="mono whitespace-nowrap px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ metricText(item.latest_metrics) }}</td>
            <td class="whitespace-nowrap px-4 py-2.5">
              <div class="flex items-center gap-1">
                <button
                  class="rounded-md border border-slate-900/10 px-2 py-1 text-xs text-slate-600 transition-colors hover:bg-slate-900/5 disabled:opacity-40 dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/10"
                  :disabled="busyId === item.id"
                  @click="toggleHidden(item)"
                >{{ item.hidden ? '恢复' : '下架' }}</button>
                <button
                  class="grid size-7 place-items-center rounded-md border border-slate-900/10 text-slate-600 transition-colors hover:bg-slate-900/5 disabled:opacity-40 dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/10"
                  title="修订标题/正文（产生新版本）"
                  :disabled="busyId === item.id"
                  @click="openEdit(item)"
                >
                  <Pencil :size="13" />
                </button>
                <button
                  class="grid size-7 place-items-center rounded-md border border-slate-900/10 text-slate-600 transition-colors hover:bg-slate-900/5 disabled:opacity-40 dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/10"
                  title="重新走 AI 审核"
                  :disabled="busyId === item.id"
                  @click="rereview(item)"
                >
                  <RotateCw :size="13" />
                </button>
                <button
                  class="grid size-7 place-items-center rounded-md border border-red-500/30 text-red-600 transition-colors hover:bg-red-500/10 disabled:opacity-40 dark:text-red-300"
                  title="彻底删除"
                  :disabled="busyId === item.id"
                  @click="deleteContent(item)"
                >
                  <Trash2 :size="13" />
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="items.length === 0 && !loading">
            <td colspan="8" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">
              暂无内容；创建目标并触发采集后，这里会出现数据。
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="totalPages > 1" class="flex items-center justify-center gap-4 text-sm">
      <button
        class="rounded-md border border-slate-900/10 px-3 py-1.5 disabled:opacity-40 dark:border-white/15"
        :disabled="page <= 1"
        @click="page--"
      >
        上一页
      </button>
      <span class="mono">{{ page }} / {{ totalPages }}</span>
      <button
        class="rounded-md border border-slate-900/10 px-3 py-1.5 disabled:opacity-40 dark:border-white/15"
        :disabled="page >= totalPages"
        @click="page++"
      >
        下一页
      </button>
    </div>

    <!-- 编辑弹层 -->
    <div v-if="editing" class="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4" @click.self="editing = null">
      <div class="panel w-full max-w-2xl p-5">
        <h2 class="text-sm font-semibold">修订内容 #{{ editing.id }}（保存后产生新版本，历史保留）</h2>
        <label class="mt-3 block text-xs font-medium text-slate-500 dark:text-zinc-400">标题</label>
        <input
          v-model="editTitle"
          class="input mt-1 w-full px-3 py-2 text-sm"
        >
        <label class="mt-3 block text-xs font-medium text-slate-500 dark:text-zinc-400">正文</label>
        <textarea
          v-model="editText"
          rows="8"
          class="textarea mt-1 w-full px-3 py-2 text-sm"
        />
        <div class="mt-4 flex justify-end gap-2">
          <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" @click="editing = null">取消</button>
          <button class="btn-primary px-4 py-2 text-sm" :disabled="editSaving" @click="saveEdit">
            {{ editSaving ? '保存中…' : '保存新版本' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
