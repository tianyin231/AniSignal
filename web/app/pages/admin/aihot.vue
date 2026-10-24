<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: 'AI 资讯管理 · AniSignal' })
interface Source { id: string; name: string; tier: string; enabled: boolean; participation_mode: string; health: string; last_ok_at: string | null; last_error: string | null; interval_minutes: number; owner_entity_id: string | null; updated_at: string; config: { feedUrl: string } }
interface Status { sources: { rows: Source[]; totals: { total: number; enabled: number; failing: number } }; pending_review: number; grouped: number; calls_24h: number }
const { api } = useAdminApi()
const toast = useAdminToast()
const data = ref<Status | null>(null)
const error = ref('')
const busy = ref(false)
const editing = ref(false)
const form = reactive({ id: '', name: '', feedUrl: '', tier: 'T2', participation_mode: 'editorial', interval_minutes: 60, owner_entity_id: '', version: '' })
const healthLabels: Record<string, string> = { ok: '正常', failing: '采集失败', pending: '等待首轮' }
async function load() {
  try { data.value = await api<Status>('/api/admin/aihot/status'); error.value = '' }
  catch { error.value = '资讯管理加载失败，请重试。' }
}
async function action(run: () => Promise<unknown>) {
  busy.value = true
  try { await run(); await load(); toast.ok('已完成') }
  catch (e: any) { toast.err(e?.data?.detail || '操作失败，请刷新后重试') }
  finally { busy.value = false }
}
function edit(source?: Source) {
  Object.assign(form, { id: '', name: '', feedUrl: '', tier: 'T2', participation_mode: 'editorial', interval_minutes: 60, owner_entity_id: '', version: '' })
  if (source) Object.assign(form, source, { feedUrl: source.config.feedUrl, version: source.updated_at, owner_entity_id: source.owner_entity_id || '' })
  editing.value = true
}
async function save() {
  await action(async () => {
    const patch = { name: form.name, kind: 'rss', tier: form.tier, config: { feedUrl: form.feedUrl }, participation_mode: form.participation_mode, interval_minutes: form.interval_minutes, owner_entity_id: form.owner_entity_id || null }
    if (form.version) await api('/api/admin/aihot/sources/' + form.id, { method: 'PATCH', body: { patch, version: form.version } })
    else await api('/api/admin/aihot/sources', { method: 'POST', body: patch })
    editing.value = false
  })
}
async function toggle(source: Source) {
  await action(() => api('/api/admin/aihot/sources/' + source.id, { method: 'PATCH', body: { version: source.updated_at, patch: { enabled: !source.enabled } } }))
}
onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <header class="wall-heading flex flex-wrap items-center justify-between gap-3"><h1 class="text-2xl font-bold text-ink">AI 资讯管理</h1><div class="flex flex-wrap gap-4 text-sm text-brand-ink"><NuxtLink to="/admin/targets">全部采集目标</NuxtLink><NuxtLink to="/admin/ai">模型配置</NuxtLink><button :disabled="busy" class="min-h-11" @click="load">刷新</button></div></header>
    <p v-if="error" role="alert" class="panel p-5 text-warn">{{ error }}</p>
    <template v-if="data">
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div class="panel p-4"><p class="text-xs text-muted">RSS 信源</p><p class="num mt-2 text-2xl text-ink">{{ data.sources.totals.enabled }}/{{ data.sources.totals.total }}</p></div>
        <div class="panel p-4"><p class="text-xs text-muted">待审核内容</p><p class="num mt-2 text-2xl text-ink">{{ data.pending_review }}</p></div>
        <div class="panel p-4"><p class="text-xs text-muted">已归组内容</p><p class="num mt-2 text-2xl text-ink">{{ data.grouped }}</p></div>
        <div class="panel p-4"><p class="text-xs text-muted">近 24 小时 AI 调用</p><p class="num mt-2 text-2xl text-ink">{{ data.calls_24h }}</p></div>
      </div>
      <AihotContentAdmin />
      <section class="panel overflow-hidden">
        <header class="flex items-center justify-between border-b border-line p-4"><h2 class="font-semibold text-ink">RSS 信源</h2><button :disabled="busy" class="min-h-11 text-sm text-brand-ink" @click="edit()">添加 RSS</button></header>
        <div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead class="bg-chip text-xs text-muted"><tr><th class="p-3">来源</th><th class="p-3">级别 / 间隔</th><th class="p-3">最近采集</th><th class="p-3">操作</th></tr></thead><tbody class="divide-y divide-line"><tr v-for="source in data.sources.rows" :key="source.id"><td class="p-3 text-ink">{{ source.name }}<span v-if="!source.enabled" class="ml-2 text-xs text-muted">已暂停</span></td><td class="p-3 text-xs text-ink-2">{{ source.tier }} · {{ source.interval_minutes }} 分钟</td><td class="p-3 text-xs" :class="source.health === 'failing' ? 'text-warn' : 'text-muted'"><span>{{ healthLabels[source.health] || source.health }}</span><span v-if="source.last_ok_at" class="block">{{ fmtTime(source.last_ok_at) }}</span><p v-if="source.last_error" class="mt-1 max-w-xs break-words">{{ source.last_error }}</p></td><td class="whitespace-nowrap p-3 text-xs text-brand-ink"><button :disabled="busy" class="min-h-11 px-2" @click="toggle(source)">{{ source.enabled ? '暂停' : '启用' }}</button><button :disabled="busy" class="min-h-11 px-2" @click="edit(source)">编辑</button><button :disabled="busy" class="min-h-11 px-2" @click="action(() => api('/api/admin/aihot/sources/' + source.id + '/fetch', { method: 'POST' }))">采集</button></td></tr></tbody></table></div>
      </section>
      <form v-if="editing" class="panel grid gap-4 p-5 sm:grid-cols-2" @submit.prevent="save">
        <h2 class="font-semibold text-ink sm:col-span-2">{{ form.version ? '编辑 RSS' : '添加 RSS' }}</h2>
        <label class="text-sm text-ink-2">名称<input v-model="form.name" required maxlength="200" class="input mt-1 min-h-11 w-full px-3"></label>
        <label class="text-sm text-ink-2">RSS 地址<input v-model="form.feedUrl" required type="url" maxlength="255" class="input mt-1 min-h-11 w-full px-3"></label>
        <label class="text-sm text-ink-2">来源级别<select v-model="form.tier" class="select mt-1 min-h-11 w-full px-3"><option value="T1">T1 · 一手来源</option><option value="T1_5">T1.5 · 近源</option><option value="T2">T2 · 媒体或其他来源</option></select></label>
        <label class="text-sm text-ink-2">参与方式<select v-model="form.participation_mode" class="select mt-1 min-h-11 w-full px-3"><option value="editorial">参与编辑与精选</option><option value="hot_signal">讨论信号</option><option value="isolated">隔离</option></select></label>
        <label class="text-sm text-ink-2">主体标识<input v-model="form.owner_entity_id" maxlength="64" class="input mt-1 min-h-11 w-full px-3"></label>
        <label class="text-sm text-ink-2">间隔（分钟）<input v-model.number="form.interval_minutes" type="number" min="5" max="1440" required class="input mt-1 min-h-11 w-full px-3"></label>
        <div class="flex gap-4"><button :disabled="busy" class="btn-primary min-h-11 px-4">{{ busy ? '保存中…' : '保存' }}</button><button type="button" :disabled="busy" class="min-h-11 px-2 text-sm text-muted" @click="editing = false">取消</button></div>
      </form>
      <AdminNewsEditorial />
    </template>
  </div>
</template>
