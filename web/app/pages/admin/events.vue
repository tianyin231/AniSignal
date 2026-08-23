<script setup lang="ts">
import type { ApiEvent, ApiEventList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { categoryMeta } from '~/utils/public-utils'

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

async function refresh() {
  try {
    const data = await api<ApiEventList>('/api/admin/events', { query: { limit: 100 } })
    events.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}
onMounted(refresh)

function pick(event: ApiEvent) {
  selected.value = event
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
      <button class="btn-primary px-4 py-2" :disabled="busy" @click="recompute">{{ busy ? '重算中…' : '重算事件' }}</button>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="grid gap-6 xl:grid-cols-[1fr_360px]">
      <div class="panel overflow-x-auto">
        <table class="w-full min-w-2xl text-sm">
          <thead>
            <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
              <th class="px-4 py-2.5 font-medium">标题</th>
              <th class="px-3 py-2.5 font-medium">类目</th>
              <th class="px-3 py-2.5 font-medium">可信度</th>
              <th class="px-3 py-2.5 font-medium">证据</th>
              <th class="px-3 py-2.5 font-medium">锁定</th>
              <th class="px-3 py-2.5 font-medium">更新</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="event in events"
              :key="event.id"
              class="cursor-pointer border-b border-slate-900/5 last:border-0 dark:border-white/5"
              :class="selected?.id === event.id ? 'bg-blue-500/5' : 'hover:bg-slate-900/5 dark:hover:bg-white/5'"
              @click="pick(event)"
            >
              <td class="max-w-72 truncate px-4 py-2.5">
                <span class="font-medium">{{ event.title }}</span>
                <span v-if="event.importance" class="chip ml-1.5 bg-red-500/10 text-red-600 dark:text-red-400">重要</span>
                <span class="mono ml-1.5 text-xs text-slate-400">#{{ event.id }}</span>
              </td>
              <td class="whitespace-nowrap px-3 py-2.5 text-slate-600 dark:text-zinc-300">{{ categoryMeta[event.category]?.label ?? event.category }}</td>
              <td class="px-3 py-2.5">
                <button class="mr-1 rounded px-1.5 py-0.5 text-xs hover:underline" :class="event.importance ? 'text-red-500 dark:text-red-400' : 'text-slate-400'" :disabled="busy" :title="event.importance ? '点击取消置顶' : '置顶到要闻最前'" @click.stop="togglePin(event)">
                  {{ event.importance ? '★ 已置顶' : '☆ 置顶' }}
                </button>
                <span class="chip" :class="{ 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400': event.confidence === 'high', 'bg-sky-500/10 text-sky-600 dark:text-sky-400': event.confidence === 'medium', 'bg-amber-500/10 text-amber-600 dark:text-amber-400': event.confidence === 'low' }">
                  {{ event.confidence }}
                </span>
              </td>
              <td class="mono px-3 py-2.5">{{ event.evidence_count }}</td>
              <td class="px-3 py-2.5">
                <button class="text-xs" :class="event.manual_locked ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400 hover:underline'" :disabled="busy" @click.stop="toggleLock(event)">
                  {{ event.manual_locked ? '已锁定 解锁' : '未锁定' }}
                </button>
              </td>
              <td class="mono whitespace-nowrap px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">{{ fmtTime(event.updated_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="selected" class="panel h-fit space-y-3 p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold">编辑事件 #{{ selected.id }}</h2>
          <NuxtLink :to="`/events/${selected.id}`" target="_blank" class="link text-xs">公开页 ↗</NuxtLink>
        </div>
        <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">标题</label>
        <input v-model="edit.title" class="w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">结论</label>
        <textarea v-model="edit.conclusion" rows="3" class="w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" />
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">可信度</label>
            <select v-model="edit.confidence" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">状态标签</label>
            <input v-model="edit.status_label" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          </div>
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="edit.importance" type="checkbox" class="rounded"> 标记为重要事件
        </label>
        <button class="btn-primary w-full px-4 py-2" :disabled="busy" @click="save">保存（自动锁定）</button>

        <div class="border-t border-slate-900/10 pt-3 dark:border-white/10">
          <p class="text-xs text-slate-500 dark:text-zinc-400">
            修正历史：{{ selected.corrections.length }} 条（最近：{{ selected.corrections.at(-1)?.note?.slice(0, 40) ?? '—' }}）
          </p>
        </div>

        <div class="border-t border-slate-900/10 pt-3 dark:border-white/10">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">合并其他事件进来</label>
          <div class="mt-1 flex gap-2">
            <input v-model="mergeSource" type="number" placeholder="源事件 ID" class="mono w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
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
