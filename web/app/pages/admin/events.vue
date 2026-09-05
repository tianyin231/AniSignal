<script setup lang="ts">
import type { ApiEvent, ApiEventList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { categoryMeta } from '~/utils/public-utils'
import { ADMIN_COMMUNITIES } from '~/utils/admin-communities'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '事件审核 · AniSignal 管理看板' })

const { api } = useAdminApi()
const events = ref<ApiEvent[]>([])
const total = ref(0)
const error = ref<string | null>(null)
const busy = ref(false)
const toast = useAdminToast()

const selected = ref<ApiEvent | null>(null)
const edit = reactive({ title: '', conclusion: '', status_label: '', importance: false, confidence: 'medium' })
const mergeSource = ref('')
const splitEvidenceIds = ref<number[]>([])
const busyAction = ref<string | null>(null)

const evidences = computed(() => {
  if (!selected.value) return []
  return [
    ...selected.value.sources.map(source => ({ ...source, role: 'support' })),
    ...selected.value.conflicts.map(source => ({ ...source, role: 'conflict' })),
  ]
})

const summaryLabels: Record<string, string> = {
  ai: 'AI 摘要',
  fallback: '安全回退',
  manual: '人工摘要',
  legacy: '历史摘要',
}

const communityFilter = ref<'' | 'wuwa' | 'blhx'>('')

async function refresh() {
  try {
    const query: Record<string, string | number> = { limit: 100 }
    if (communityFilter.value) query.community = communityFilter.value
    const data = await api<ApiEventList>('/api/admin/events', { query })
    events.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}
watch(communityFilter, refresh)
onMounted(refresh)

function pick(event: ApiEvent) {
  selected.value = event
  splitEvidenceIds.value = []
  edit.title = event.title
  edit.conclusion = event.conclusion ?? ''
  edit.status_label = event.status_label ?? ''
  edit.importance = event.importance
  edit.confidence = event.confidence
}

async function save() {
  if (!selected.value) return
  busy.value = true
  try {
    await api(`/api/admin/events/${selected.value.id}`, { method: 'PUT', body: { ...edit, conclusion: edit.conclusion || null, status_label: edit.status_label || null } })
    toast.ok('事件已保存并锁定')
    await refresh()
    const updated = events.value.find((e) => e.id === selected.value!.id)
    if (updated) pick(updated)
  } catch (exc: any) {
    error.value = `保存失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
  }
}

async function togglePin(event: ApiEvent) {
  busy.value = true
  try {
    await api(`/api/admin/events/${event.id}`, { method: 'PUT', body: { importance: !event.importance } })
    toast.ok(event.importance ? '已取消置顶' : '已置顶（排在要闻最前）')
    await refresh()
  } catch (exc: any) {
    toast.err(`操作失败：${exc?.data?.detail ?? exc?.message}`)
  } finally {
    busy.value = false
  }
}

async function toggleLock(event: ApiEvent) {
  busy.value = true
  try {
    await api(`/api/admin/events/${event.id}/lock`, { method: 'POST', query: { locked: !event.manual_locked } })
    toast.ok(event.manual_locked ? '已解锁，恢复引擎维护' : '已锁定')
    await refresh()
    const updated = events.value.find((e) => e.id === event.id)
    if (updated && selected.value?.id === event.id) pick(updated)
  } catch (exc: any) {
    error.value = `操作失败：${exc?.message}`
  } finally {
    busy.value = false
  }
}

async function recompute() {
  busy.value = true
  error.value = null
  try {
    toast.info('事件重算执行中…')
    const result = await api('/api/admin/events/recompute', { method: 'POST' })
    toast.ok(`重算完成：新建 ${result.events_created} · 证据 +${result.evidences_added} · 修订 +${result.revisions_written}`)
    await refresh()
  } catch (exc: any) {
    error.value = `重算失败：${exc?.message}`
  } finally {
    busy.value = false
  }
}

async function merge() {
  if (!selected.value || !mergeSource.value) return
  const sourceId = Number(mergeSource.value)
  if (!confirm(`把事件 #${sourceId} 合并进 #${selected.value.id}？源事件将停用。`)) return
  busy.value = true
  try {
    await api('/api/admin/events/merge', { method: 'POST', body: { source_id: sourceId, target_id: selected.value.id } })
    toast.ok(`事件 #${sourceId} 已合并进来`)
    mergeSource.value = ''
    await refresh()
  } catch (exc: any) {
    error.value = `合并失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
  }
}

async function regenerateSummary() {
  if (!selected.value) return
  busy.value = true
  busyAction.value = 'summary'
  error.value = null
  try {
    const result = await api<{ summary_status: string }>(`/api/admin/events/${selected.value.id}/summarize`, { method: 'POST' })
    toast.ok(result.summary_status === 'fallback' ? '模型结果未通过校验，已使用安全回退摘要' : 'AI 摘要已重新生成')
    const eventId = selected.value.id
    await refresh()
    const updated = events.value.find(event => event.id === eventId)
    if (updated) pick(updated)
  } catch (exc: any) {
    error.value = `摘要生成失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
    busyAction.value = null
  }
}

async function removeEvidence(contentId: number) {
  if (!selected.value || !confirm(`从事件 #${selected.value.id} 移除证据内容 #${contentId}？该内容将不再参与自动聚合。`)) return
  busy.value = true
  busyAction.value = `remove-${contentId}`
  error.value = null
  const eventId = selected.value.id
  try {
    const result = await api<{ deactivated: boolean }>(`/api/admin/events/${eventId}/evidences/${contentId}`, { method: 'DELETE' })
    toast.ok(result.deactivated ? '最后一条证据已移除，事件已停用' : '证据已移除并记录排除')
    await refresh()
    const updated = events.value.find(event => event.id === eventId)
    if (updated) pick(updated)
    else selected.value = null
  } catch (exc: any) {
    error.value = `移除失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
    busyAction.value = null
  }
}

async function splitSelectedEvidence() {
  if (!selected.value || !splitEvidenceIds.value.length) return
  if (!confirm(`将所选 ${splitEvidenceIds.value.length} 条证据从事件 #${selected.value.id} 拆成新事件？`)) return
  busy.value = true
  busyAction.value = 'split'
  error.value = null
  try {
    const created = await api<ApiEvent>(`/api/admin/events/${selected.value.id}/split`, {
      method: 'POST',
      body: { content_ids: splitEvidenceIds.value },
    })
    toast.ok(`已拆分为新事件 #${created.id}，两个事件均已锁定`)
    await refresh()
    const updated = events.value.find(event => event.id === created.id)
    if (updated) pick(updated)
  } catch (exc: any) {
    error.value = `拆分失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
    busyAction.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-lg font-semibold">事件审核</h1>
        <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
          引擎自动聚合，人工复核后编辑会自动锁定（引擎不再覆盖）。共 {{ total }} 条。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-model="communityFilter"
          class="select px-3 py-2 text-sm"
        >
          <option value="">全部分区</option>
          <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
        <button class="btn-primary px-4 py-2" :disabled="busy" @click="recompute">{{ busy ? '重算中…' : '重算事件' }}</button>
      </div>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="grid gap-6 xl:grid-cols-[1fr_360px]">
      <div class="panel divide-y divide-slate-900/5 dark:divide-white/10">
        <div
          v-for="event in events"
          :key="event.id"
          class="cursor-pointer px-4 py-3 transition-colors"
          :class="selected?.id === event.id ? 'bg-blue-500/5' : 'hover:bg-slate-900/[0.04] dark:hover:bg-white/5'"
          @click="pick(event)"
        >
          <div class="flex items-start justify-between gap-3">
            <p class="min-w-0 truncate text-sm font-medium text-slate-800 dark:text-zinc-200">
              {{ event.title }}
              <span v-if="event.importance" class="chip ml-1 bg-red-500/10 text-red-600 dark:text-red-400">重要</span>
              <span class="mono ml-1 text-xs font-normal text-slate-400">#{{ event.id }}</span>
            </p>
            <span class="mono shrink-0 text-xs text-slate-400">{{ fmtTime(event.updated_at) }}</span>
          </div>
          <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400 dark:text-zinc-500">
            <span class="text-slate-600 dark:text-zinc-300">{{ categoryMeta[event.category]?.label ?? event.category }}</span>
            <span aria-hidden="true">·</span>
            <span>证据 {{ event.evidence_count }}</span>
            <span aria-hidden="true">·</span>
            <span
              class="rounded px-1.5 py-0.5"
              :class="event.confidence === 'high' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : event.confidence === 'medium' ? 'bg-sky-500/10 text-sky-600 dark:text-sky-400' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'"
            >可信度 {{ event.confidence }}</span>
            <span aria-hidden="true">·</span>
            <span
              class="rounded px-1.5 py-0.5"
              :class="event.summary_status === 'fallback' ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300' : event.summary_status === 'ai' ? 'bg-blue-500/10 text-blue-600 dark:text-blue-300' : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
            >{{ summaryLabels[event.summary_status ?? ''] ?? '未生成' }}</span>
            <span aria-hidden="true">·</span>
            <button class="hover:underline" :class="event.importance ? 'text-red-500 dark:text-red-400' : 'text-slate-400'" :disabled="busy" :title="event.importance ? '点击取消置顶' : '置顶到要闻最前'" @click.stop="togglePin(event)">
              {{ event.importance ? '★ 已置顶' : '☆ 置顶' }}
            </button>
            <span aria-hidden="true">·</span>
            <button class="hover:underline" :class="event.manual_locked ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'" :disabled="busy" @click.stop="toggleLock(event)">
              {{ event.manual_locked ? '已锁定 解锁' : '未锁定' }}
            </button>
          </div>
        </div>
        <div v-if="!events.length" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">
          暂无事件。
        </div>
      </div>

      <div v-if="selected" class="panel h-fit space-y-3 p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold">编辑事件 #{{ selected.id }}</h2>
          <NuxtLink :to="`/events/${selected.id}`" target="_blank" class="link text-xs">公开页 ↗</NuxtLink>
        </div>
        <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">标题</label>
        <input v-model="edit.title" class="input w-full px-3 py-2 text-sm">
        <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">结论</label>
        <textarea v-model="edit.conclusion" rows="3" class="textarea w-full px-3 py-2 text-sm" />
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">可信度</label>
            <select v-model="edit.confidence" class="select mt-1 w-full px-3 py-2 text-sm">
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">状态标签</label>
            <input v-model="edit.status_label" class="input mt-1 w-full px-3 py-2 text-sm">
          </div>
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="edit.importance" type="checkbox" class="checkbox"> 标记为重要事件
        </label>
        <button class="btn-primary w-full px-4 py-2" :disabled="busy" @click="save">保存（自动锁定）</button>

        <div class="space-y-2 border-t border-slate-900/10 pt-3 dark:border-white/10">
          <div class="flex items-center justify-between">
            <label class="text-xs font-medium text-slate-500 dark:text-zinc-400">摘要质量</label>
            <span
              class="chip"
              :class="selected.summary_status === 'fallback' ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300' : selected.summary_status === 'ai' ? 'bg-blue-500/10 text-blue-600 dark:text-blue-300' : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
            >{{ summaryLabels[selected.summary_status ?? ''] ?? '未生成' }}</span>
          </div>
          <p class="rounded-md bg-slate-900/[0.03] px-3 py-2 text-sm leading-6 dark:bg-white/5">
            {{ selected.summary || '当前没有摘要。' }}
          </p>
          <p class="text-xs text-slate-500 dark:text-zinc-400">
            {{ selected.evidence_count }} 条证据 · 生成于 {{ fmtTime(selected.summary_updated_at) }}
          </p>
          <button class="min-h-11 w-full rounded-md border border-slate-900/10 px-3 py-2 text-sm dark:border-white/15" :disabled="busy" @click="regenerateSummary">
            {{ busyAction === 'summary' ? '生成中…' : '重新生成摘要' }}
          </button>
        </div>

        <div class="space-y-2 border-t border-slate-900/10 pt-3 dark:border-white/10">
          <div class="flex items-center justify-between">
            <label class="text-xs font-medium text-slate-500 dark:text-zinc-400">证据处理</label>
            <span class="mono text-xs text-slate-400">已选 {{ splitEvidenceIds.length }}</span>
          </div>
          <p class="text-xs leading-5 text-slate-500 dark:text-zinc-400">应独立成事件的证据请选择“拆分”；无关内容请选择“移除”。</p>
          <div class="max-h-72 space-y-2 overflow-y-auto pr-1">
            <div v-for="source in evidences" :key="`${source.role}-${source.content_id}`" class="rounded-md border border-slate-900/10 p-2.5 dark:border-white/10">
              <div class="flex items-start gap-2">
                <input v-if="source.content_id" v-model="splitEvidenceIds" type="checkbox" :value="source.content_id" class="checkbox mt-1" :disabled="busy" :aria-label="`选择证据内容 #${source.content_id}`">
                <div class="min-w-0 flex-1">
                  <p class="truncate text-sm font-medium">{{ source.title || `内容 #${source.content_id}` }}</p>
                  <p class="mt-0.5 text-xs text-slate-500 dark:text-zinc-400">
                    {{ source.platform }} · {{ source.account || '未知来源' }} · {{ fmtTime(source.published_at) }}
                    <span v-if="source.role === 'conflict'" class="ml-1 text-red-500">冲突证据</span>
                  </p>
                </div>
              </div>
              <div v-if="source.content_id" class="mt-2 flex justify-end gap-3 text-xs">
                <NuxtLink :to="`/contents/${source.content_id}`" target="_blank" class="link inline-flex min-h-11 items-center px-1">查看内容 ↗</NuxtLink>
                <button class="min-h-11 px-1 text-red-600 hover:underline dark:text-red-400" :disabled="busy" @click="removeEvidence(source.content_id)">
                  {{ busyAction === `remove-${source.content_id}` ? '移除中…' : '移除' }}
                </button>
              </div>
            </div>
          </div>
          <button
            class="min-h-11 w-full rounded-md border border-blue-500/30 px-3 py-2 text-sm text-blue-600 dark:text-blue-300"
            :disabled="busy || !splitEvidenceIds.length || splitEvidenceIds.length >= selected.evidence_count"
            @click="splitSelectedEvidence"
          >{{ busyAction === 'split' ? '拆分中…' : `拆分所选证据（${splitEvidenceIds.length}）` }}</button>
        </div>

        <div class="border-t border-slate-900/10 pt-3 dark:border-white/10">
          <p class="text-xs text-slate-500 dark:text-zinc-400">
            修正历史：{{ selected.corrections.length }} 条（最近：{{ selected.corrections.at(-1)?.note?.slice(0, 40) ?? '—' }}）
          </p>
        </div>

        <div class="border-t border-slate-900/10 pt-3 dark:border-white/10">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">合并其他事件进来</label>
          <div class="mt-1 flex gap-2">
            <input v-model="mergeSource" type="number" placeholder="源事件 ID" class="input mono w-full px-3 py-2 text-sm">
            <button class="shrink-0 rounded-md border border-slate-900/10 px-3 py-2 text-sm dark:border-white/15" :disabled="busy || !mergeSource" @click="merge">合并</button>
          </div>
        </div>
      </div>
      <div v-else class="panel px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">
        从左侧选择一个事件进行复核或修正。
      </div>
    </div>
  </div>
</template>
