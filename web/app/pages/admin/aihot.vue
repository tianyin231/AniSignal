<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: 'AI 新闻引擎 · AniSignal 管理看板' })
interface Source { id: string; name: string; kind: string; tier: string; participation_mode: string; enabled: boolean; health: string; items_7d: number; selected_30d: number; last_ok_at: string | null; last_error: string | null }
interface Budget { service: string; per_minute: number; per_hour: number; per_day: number; used_day: number }
interface Capability { key: string; label: string; current: { model: string }; usage: { purpose: string; calls: number; ok: number; failed: number; tokensIn: number; tokensOut: number }[] }
interface Status { enabled: boolean; read_enabled: boolean; upstream: string; sources?: { rows: Source[]; totals: { total: number; enabled: number; failing: number } }; budgets?: Budget[]; models?: { capabilities: Capability[]; choices: { key: string }[] }; runs?: { grouping: { waiting: number; needsAttention: number }; receipts: { counts: Record<string, number> }; processes: { role: string; alive: boolean }[] } }
const { api } = useAdminApi()
const toast = useAdminToast()
const data = ref<Status | null>(null)
const error = ref('')
const busy = ref(false)
const editing = ref(false)
const form = reactive({ id: '', name: '', tier: 'T2', participation_mode: 'editorial', interval_minutes: 60, feedUrl: '', version: '', kind: 'rss', owner_entity_id: '' })
async function load() {
  try { data.value = await api<Status>('/api/admin/aihot/status'); error.value = '' }
  catch { error.value = '引擎暂不可用，请检查服务状态。' }
}
async function action(run: () => Promise<unknown>) {
  busy.value = true
  try { await run(); await load(); toast.ok('已保存') }
  catch { toast.err('操作失败，请刷新后重试') }
  finally { busy.value = false }
}
async function toggle(source: Source) {
  await action(async () => {
    const detail = await api<{ source: { updated_at: string } }>(`/api/admin/aihot/sources/${source.id}`)
    await api(`/api/admin/aihot/sources/${source.id}`, { method: 'PATCH', body: { version: detail.source.updated_at, patch: { enabled: !source.enabled }, reason: '管理员调整信源开关' } })
  })
}
async function edit(source?: Source) {
  Object.assign(form, { id: '', name: '', tier: 'T2', participation_mode: 'editorial', interval_minutes: 60, feedUrl: '', version: '', kind: 'rss', owner_entity_id: '' })
  if (source) {
    const { source: detail } = await api<{ source: Source & { updated_at: string; interval_minutes: number; config: { feedUrl: string } } }>(`/api/admin/aihot/sources/${source.id}`)
    Object.assign(form, detail, { version: detail.updated_at, feedUrl: detail.config.feedUrl })
  }
  editing.value = true
}
async function saveSource() {
  await action(async () => {
    if (form.version) {
      const { source } = await api<{ source: { config: Record<string, unknown> } }>(`/api/admin/aihot/sources/${form.id}`)
      await api(`/api/admin/aihot/sources/${form.id}`, { method: 'PATCH', body: { version: form.version, reason: '管理员编辑新闻源', patch: { name: form.name, tier: form.tier, participation_mode: form.participation_mode, interval_minutes: form.interval_minutes, owner_entity_id: form.owner_entity_id || null, ...(form.kind === 'rss' ? { config: { ...source.config, feedUrl: form.feedUrl } } : {}) } } })
    } else {
      const result = await api<{ created: boolean }>('/api/admin/aihot/sources', { method: 'POST', body: { id: form.id || `rss-${Date.now()}`, name: form.name, kind: 'rss', config: { feedUrl: form.feedUrl }, tier: form.tier, participation_mode: form.participation_mode, interval_minutes: form.interval_minutes, site_fulltext: false, syndicate_fulltext: false } })
      if (!result.created) throw new Error('重复信源')
    }
    editing.value = false
  })
}
onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <header class="wall-heading flex items-center justify-between"><div><h1 class="text-2xl font-bold text-ink">AI 新闻引擎</h1><p class="mt-2 text-sm text-muted">{{ data?.read_enabled ? '网页与 Bot 正在读取原版发布层' : '对照运行，尚未切换公开数据' }} · {{ data?.upstream }}</p></div><button class="rounded-lg border border-line px-4 py-2 text-sm text-ink" @click="load">刷新</button></header>
    <p v-if="error" class="panel p-5 text-warn">{{ error }}</p>
    <p v-if="data && !data.enabled" class="panel p-5 text-muted">引擎尚未配置。</p>
    <template v-if="data?.enabled">
      <AihotContentAdmin />
      <div class="grid gap-3 sm:grid-cols-4"><div class="panel p-4"><p class="text-xs text-muted">新闻源</p><p class="num mt-2 text-2xl text-ink">{{ data.sources?.totals.enabled }}/{{ data.sources?.totals.total }}</p></div><div class="panel p-4"><p class="text-xs text-muted">等待归组</p><p class="num mt-2 text-2xl text-ink">{{ data.runs?.grouping.waiting }}</p></div><div class="panel p-4"><p class="text-xs text-muted">需处理</p><p class="num mt-2 text-2xl text-ink">{{ data.runs?.grouping.needsAttention }}</p></div><div class="panel p-4"><p class="text-xs text-muted">进程</p><p v-for="p in data.runs?.processes" :key="p.role" class="mt-1 text-xs" :class="p.alive ? 'text-ok' : 'text-warn'">{{ p.role }} · {{ p.alive ? '在线' : '离线' }}</p></div></div>
      <section class="panel overflow-hidden"><header class="flex justify-between border-b border-line p-4"><h2 class="font-semibold text-ink">信源</h2><button class="text-sm text-brand-ink" @click="edit()">添加 RSS</button></header><div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead class="bg-chip text-xs text-muted"><tr><th class="p-3">来源</th><th>级别 / 参与方式</th><th>健康</th><th>7 天入库 / 30 天精选</th><th class="p-3">操作</th></tr></thead><tbody class="divide-y divide-line"><tr v-for="source in data.sources?.rows" :key="source.id"><td class="p-3 text-ink">{{ source.name }}<span class="mt-1 block text-xs text-muted">{{ source.kind }}</span></td><td class="text-xs text-ink-2">{{ source.tier }} · {{ source.participation_mode }}</td><td :title="source.last_error || ''" class="text-xs" :class="source.health === 'failing' ? 'text-warn' : 'text-muted'">{{ source.health }}<span v-if="source.last_ok_at" class="block">{{ fmtTime(source.last_ok_at) }}</span></td><td class="num text-ink-2">{{ source.items_7d }} / {{ source.selected_30d }}</td><td class="space-x-3 whitespace-nowrap p-3 text-xs text-brand-ink"><button v-if="source.kind !== 'external'" :disabled="busy" @click="toggle(source)">{{ source.enabled ? '暂停' : '启用' }}</button><button @click="edit(source)">编辑</button><button v-if="source.kind !== 'external'" :disabled="busy" @click="action(() => api(`/api/admin/aihot/sources/${source.id}/fetch`, { method: 'POST' }))">采集</button></td></tr></tbody></table></div></section>
      <form v-if="editing" class="panel grid gap-4 p-5 sm:grid-cols-2" @submit.prevent="saveSource"><h2 class="font-semibold text-ink sm:col-span-2">{{ form.version ? '编辑来源' : '添加 RSS' }}</h2><label class="text-sm text-ink-2">名称<input v-model="form.name" required maxlength="200" class="mt-1 block w-full rounded-lg border border-line bg-card p-2"></label><label v-if="form.kind === 'rss'" class="text-sm text-ink-2">RSS 地址<input v-model="form.feedUrl" required type="url" class="mt-1 block w-full rounded-lg border border-line bg-card p-2"></label><label class="text-sm text-ink-2">来源级别<select v-model="form.tier" class="mt-1 block w-full rounded-lg border border-line bg-card p-2"><option value="T1">T1 · 已确认的一手机构</option><option value="T1_5">T1.5 · 团队成员等近源</option><option value="T2">T2 · 媒体与其他来源</option></select></label><label class="text-sm text-ink-2">参与方式<select v-model="form.participation_mode" class="mt-1 block w-full rounded-lg border border-line bg-card p-2"><option value="editorial">参与编辑与精选</option><option value="hot_signal">只计讨论热度</option><option value="isolated">隔离</option></select></label><label class="text-sm text-ink-2">同一运营主体（可选）<input v-model="form.owner_entity_id" maxlength="120" class="mt-1 block w-full rounded-lg border border-line bg-card p-2"></label><label class="text-sm text-ink-2">间隔（分钟）<input v-model.number="form.interval_minutes" required type="number" min="1" max="1440" class="mt-1 block w-full rounded-lg border border-line bg-card p-2"></label><div class="flex items-end gap-3"><button :disabled="busy" class="rounded-lg bg-ink px-4 py-2 text-sm text-card">保存</button><button type="button" class="text-sm text-muted" @click="editing = false">取消</button></div></form>
      <section class="panel p-5"><h2 class="font-semibold text-ink">调用预算与回执</h2><p class="mt-2 text-xs text-muted">滚动窗口计数，失败尝试也占预算；设为 0 暂停该服务。</p><form v-for="budget in data.budgets" :key="budget.service" class="mt-4 flex flex-wrap items-end gap-3" @submit.prevent="action(() => api(`/api/admin/aihot/budgets/${budget.service}`, { method: 'PUT', body: { perMinute: budget.per_minute, perHour: budget.per_hour, perDay: budget.per_day, reason: '管理员调整调用预算' } }))"><span class="w-24 text-sm text-ink">{{ budget.service }}</span><label v-for="field in (['per_minute', 'per_hour', 'per_day'] as const)" :key="field" class="text-xs text-muted">{{ { per_minute: '每分钟', per_hour: '每小时', per_day: '每天' }[field] }}<input v-model.number="budget[field]" type="number" min="0" required class="mt-1 block w-24 rounded-md border border-line bg-card p-2 text-sm text-ink"></label><button :disabled="busy" class="rounded-lg border border-line px-3 py-2 text-sm text-ink">保存</button><span class="text-xs text-muted">近 24h 已用 {{ budget.used_day }}</span></form><p class="mt-4 text-xs text-muted">回执：<span v-for="(count, state) in data.runs?.receipts.counts" :key="state" class="mr-3">{{ state }} {{ count }}</span></p></section>
      <section class="panel p-5"><h2 class="font-semibold text-ink">各步骤模型与用量</h2><p class="mt-2 text-xs text-muted">default 使用部署配置；具名模型需先配置对应凭据。</p><div v-for="capability in data.models?.capabilities" :key="capability.key" class="mt-4 border-t border-line pt-4"><label class="flex flex-wrap items-center justify-between gap-3 text-sm text-ink">{{ capability.label }}<select :value="capability.current.model" :disabled="busy" class="rounded-md border border-line bg-card p-2" @change="action(() => api(`/api/admin/aihot/models/${capability.key}`, { method: 'PUT', body: { model: ($event.target as HTMLSelectElement).value, reason: '管理员切换步骤模型' } }))"><option v-for="model in data.models?.choices" :key="model.key" :value="model.key">{{ model.key }}</option></select></label><p v-for="usage in capability.usage" :key="usage.purpose" class="mt-2 text-xs text-muted">{{ usage.purpose }} · {{ usage.calls }} 次 · 成功 {{ usage.ok }} / 失败 {{ usage.failed }} · {{ usage.tokensIn + usage.tokensOut }} tokens</p></div></section>
    </template>
  </div>
</template>
