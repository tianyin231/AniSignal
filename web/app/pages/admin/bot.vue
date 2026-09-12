<script setup lang="ts">
import { ADMIN_COMMUNITIES, ADMIN_COMMUNITY_LABELS } from '~/utils/admin-communities'
import { fmtTime } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '机器人通知 · AniSignal' })
const { api } = useAdminApi()
const toast = useAdminToast()
interface Target {
  id?: number; name: string; umo: string; enabled: boolean; community: string; keywords: string[]
  daily_enabled: boolean; daily_time: string; updates_enabled: boolean; quiet_start: number; quiet_end: number
  tools: string[]; ai_prompt: string; ai_provider_id: string
  delivery_mode: string; chat_controls: boolean; reminders_enabled: boolean; activities_enabled: boolean; manager_ids: string[]
}
interface Delivery { id: number; target_id: number; name: string; kind: string; status: string; error: string | null; body: string; rendered_body?: string; attempts: number; created_at: string }
interface Reminder { id: number; name: string; body: string; due_at: string; status: string }
const companion = ref<{ runtime: { online: boolean; version?: string }; counts: Record<string, number>; reminders: Reminder[] }>({ runtime: { online: false }, counts: {}, reminders: [] })
const activity = ref<Record<string, string>>({})
const defaults = (): Target => ({ name: '', umo: '', enabled: false, community: 'all', keywords: [], daily_enabled: true, daily_time: '08:00', updates_enabled: false, quiet_start: 23, quiet_end: 8, tools: ['digest', 'news', 'products'], ai_prompt: '简洁、自然地回答，保留来源与时间。', ai_provider_id: '', delivery_mode: 'astrbot', chat_controls: false, reminders_enabled: false, activities_enabled: false, manager_ids: [] })
const targets = ref<Target[]>([])
const deliveries = ref<Delivery[]>([])
const bridge = reactive({ endpoint: '', api_key: '', api_key_set: false })
const toolNames: Record<string, string> = { digest: '今日速报', news: '资讯查询', products: '商品进展' }
const statuses: Record<string, string> = { pending: '待发送', sending: '发送中', accepted: '网关已接收', failed: '发送失败', unknown: '结果待核对', cancelled: '已取消' }
const form = ref<Target>(defaults())
const editorOpen = ref(false)
const editorDialog = ref<HTMLDialogElement | null>(null)
const editorError = ref('')
let previousBodyOverflow = ''
watch(editorOpen, (open) => {
  if (open) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    editorDialog.value?.showModal()
  } else {
    editorDialog.value?.close()
    document.body.style.overflow = previousBodyOverflow
  }
}, { flush: 'post' })
onBeforeUnmount(() => {
  if (editorOpen.value) document.body.style.overflow = previousBodyOverflow
})

function closeEditor() {
  if (!busy.value) editorOpen.value = false
}
function onDialogClick(event: MouseEvent) {
  const dialog = editorDialog.value
  if (!dialog || event.target !== dialog) return
  const rect = dialog.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeEditor()
}
const keywordInput = ref('')
const managerInput = ref('')
const busy = ref('')
const loading = ref(true)
const loadError = ref('')
const selectedId = ref<number | null>(null)
const tool = ref('digest')
const query = ref('')
const previewText = ref('')
const selected = computed(() => targets.value.find(t => t.id === selectedId.value))
const activeCount = computed(() => targets.value.filter(t => t.enabled).length)
const inputClass = 'mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/15 dark:bg-zinc-900'

function umoTail(umo: string) {
  return (umo.split(':')[2] || '').slice(-8) || umo
}
function relTime(iso?: string) {
  if (!iso) return '从未活跃'
  const ts = new Date(/[z+]/i.test(iso) ? iso : `${iso}Z`).getTime()
  if (!Number.isFinite(ts)) return '从未活跃'
  const minutes = Math.floor((Date.now() - ts) / 60000)
  if (minutes < 1) return '刚刚活跃'
  if (minutes < 60) return `${minutes} 分钟前活跃`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前活跃`
  if (minutes < 10080) return `${Math.floor(minutes / 1440)} 天前活跃`
  return `最近活跃 ${fmtTime(iso)}`
}

async function refresh() {
  loading.value = true
  loadError.value = ''
  try {
    const [data, logs, companionData] = await Promise.all([
      api<{ bridge: { endpoint: string; api_key_set: boolean }; targets: Target[]; activity: Record<string, string> }>('/api/admin/bot'),
      api<Delivery[]>('/api/admin/bot/deliveries'),
      api<typeof companion.value>('/api/admin/bot/companion'),
    ])
    targets.value = data.targets
    activity.value = data.activity || {}
    Object.assign(bridge, data.bridge)
    deliveries.value = logs
    companion.value = companionData
    if (!targets.value.some(t => t.id === selectedId.value)) selectedId.value = targets.value[0]?.id ?? null
  } catch (e: any) { loadError.value = e?.data?.detail || e.message }
  finally { loading.value = false }
}
onMounted(refresh)
watch([selectedId, tool, query], () => { previewText.value = '' })

async function action(key: string, fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = key
  try { await fn() }
  catch (e: any) {
    const detail = e?.data?.detail
    const message = typeof detail === 'string' ? detail : '操作失败，请检查输入与网络'
    if (key === 'target') editorError.value = message
    else toast.err(message)
  } finally { busy.value = '' }
}
function edit(target?: Target) {
  form.value = target ? structuredClone(toRaw(target)) : defaults()
  keywordInput.value = form.value.keywords.join('，')
  managerInput.value = form.value.manager_ids.join('，')
  editorError.value = ''
  editorOpen.value = true
}
function saveTarget() {
  action('target', async () => {
    editorError.value = ''
    form.value.keywords = keywordInput.value.split(/[,，\n]/).map(s => s.trim()).filter(Boolean)
    form.value.manager_ids = managerInput.value.split(/[,，\n]/).map(s => s.trim()).filter(Boolean)
    const id = form.value.id
    const saved = await api<Target>(`/api/admin/bot/targets${id ? `/${id}` : ''}`, { method: id ? 'PUT' : 'POST', body: form.value })
    editorOpen.value = false
    selectedId.value = saved.id!
    previewText.value = ''
    toast.ok('会话配置已保存')
    await refresh()
  })
}
function toggle(target: Target) {
  action(`toggle-${target.id}`, async () => {
    await api(`/api/admin/bot/targets/${target.id}`, { method: 'PUT', body: { ...target, enabled: !target.enabled } })
    toast.ok(target.enabled ? '已暂停该会话' : '已启用该会话')
    await refresh()
  })
}
function remove(target: Target) {
  if (!confirm(`删除「${target.name}」及其投递记录？`)) return
  action('delete', async () => {
    await api(`/api/admin/bot/targets/${target.id}`, { method: 'DELETE' })
    toast.ok('已删除会话')
    await refresh()
  })
}
function saveBridge() {
  action('bridge', async () => {
    await api('/api/admin/bot/bridge', { method: 'PUT', body: { endpoint: bridge.endpoint, api_key: bridge.api_key } })
    bridge.api_key = ''
    toast.ok('AstrBot 通道已保存')
    await refresh()
  })
}
function preview() {
  action('preview', async () => {
    const result = await api<{ text: string }>(`/api/admin/bot/targets/${selectedId.value}/preview`, { method: 'POST', body: { tool: tool.value, query: query.value } })
    previewText.value = result.text
    toast.ok('查询完成，可编辑后发送')
  })
}
function send() {
  action('send', async () => {
    const result = await api<{ status: string; error: string }>(`/api/admin/bot/targets/${selectedId.value}/send`, { method: 'POST', body: { content: previewText.value } })
    if (result.status === 'accepted') toast.ok('AstrBot 已接收，请在 QQ 核对消息')
    else if (result.status === 'pending') toast.ok('已排队，AstrBot 将按当前人格播报')
    else toast.err(result.error || '发送失败')
    await refresh()
  })
}
function retry(row: Delivery) {
  if (!confirm('请先核对收件端，重试可能产生重复消息。现在重新发送？')) return
  action(`retry-${row.id}`, async () => {
    const result = await api<{ status: string; error: string }>(`/api/admin/bot/deliveries/${row.id}/retry`, { method: 'POST' })
    if (result.status === 'accepted') toast.ok('AstrBot 已接收')
    else if (result.status === 'pending') toast.ok('已重新排队')
    else toast.err(result.error || '发送失败')
    await refresh()
  })
}
function cancelReminder(row: Reminder) {
  action(`reminder-${row.id}`, async () => {
    await api(`/api/admin/bot/reminders/${row.id}/cancel`, { method: 'POST' })
    toast.ok('提醒已取消')
    await refresh()
  })
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div><h1 class="text-2xl font-semibold">机器人通知</h1><p class="mt-1 text-sm text-slate-500">{{ targets.length }} 个会话 · {{ activeCount }} 个已启用</p></div>
      <div class="flex gap-2"><button class="rounded-lg border px-3 py-2 text-sm" :disabled="loading || !!busy" @click="refresh">{{ loading ? '加载中…' : '刷新' }}</button><button class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" :disabled="!!busy" @click="edit()">添加会话</button></div>
    </div>
    <p v-if="loadError" role="alert" class="rounded-lg bg-red-500/10 p-3 text-red-600">{{ loadError }}</p>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div class="panel rounded-xl p-4"><p class="text-sm text-slate-500">AstrBot 插件</p><p class="mt-2 font-semibold" :class="companion.runtime.online ? 'text-emerald-600' : 'text-amber-600'">{{ companion.runtime.online ? `在线 · ${companion.runtime.version}` : '未连接或心跳延迟' }}</p></div>
      <div class="panel rounded-xl p-4"><p class="text-sm text-slate-500">近 24 小时已接收</p><p class="mt-2 text-xl font-semibold">{{ companion.counts.accepted || 0 }}</p></div>
      <div class="panel rounded-xl p-4"><p class="text-sm text-slate-500">近 24 小时待处理</p><p class="mt-2 text-xl font-semibold">{{ (companion.counts.pending || 0) + (companion.counts.sending || 0) }}</p></div>
      <div class="panel rounded-xl p-4"><p class="text-sm text-slate-500">近 24 小时需核对</p><p class="mt-2 text-xl font-semibold">{{ (companion.counts.failed || 0) + (companion.counts.unknown || 0) }}</p></div>
    </div>

    <details class="panel rounded-xl p-5" :open="!bridge.api_key_set">
      <summary class="cursor-pointer font-medium">AstrBot 发送通道 <span class="ml-2 text-sm text-slate-500">{{ bridge.api_key_set ? '已配置密钥' : '待配置' }}</span></summary>
      <form class="mt-4 grid gap-4 sm:grid-cols-2" @submit.prevent="saveBridge">
        <label class="text-sm">发送接口<input v-model.trim="bridge.endpoint" type="url" required :class="inputClass" placeholder="http://astrbot:6185/api/v1/im/messages"></label>
        <label class="text-sm">API 密钥<input v-model="bridge.api_key" type="password" autocomplete="new-password" :class="inputClass" :placeholder="bridge.api_key_set ? '已保存，留空保持原值' : '填写 AstrBot API Key'"></label>
        <div><button class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" :disabled="!!busy">{{ busy === 'bridge' ? '保存中…' : '保存通道' }}</button></div>
      </form>
    </details>

    <div v-if="!targets.length && !loading" class="panel rounded-xl p-10 text-center text-slate-500">添加第一个群聊或私聊，设置它关心的资讯。</div>
    <div class="grid gap-4 sm:grid-cols-2">
      <article v-for="target in targets" :key="target.id" class="panel rounded-xl p-5">
        <div class="flex items-start justify-between gap-3"><div><h2 class="font-semibold">{{ target.name }}</h2><p class="mt-1 text-sm text-slate-500">{{ target.umo.includes(':GroupMessage:') ? '群聊' : '私聊' }} · {{ ADMIN_COMMUNITY_LABELS[target.community] || '全站' }} · <span class="mono" :title="target.umo">{{ target.umo.split(':')[0] }} · 尾号 {{ umoTail(target.umo) }}</span></p></div><span class="rounded-full px-2 py-1 text-xs" :class="target.enabled ? 'bg-emerald-500/10 text-emerald-600' : 'bg-slate-500/10 text-slate-500'">{{ target.enabled ? '已启用' : '已暂停' }}</span></div>
        <p class="mt-3 text-sm">{{ target.daily_enabled ? `每日 ${target.daily_time} 早报` : '早报关闭' }} · {{ target.updates_enabled ? '重要动态推送' : '即时推送关闭' }}</p>
        <p class="mt-2 text-sm" :class="activity[target.umo] ? 'text-slate-500' : 'text-amber-600'">{{ relTime(activity[target.umo]) }}</p>
        <p class="mt-2 text-sm text-slate-500">{{ target.keywords.length ? target.keywords.join(' / ') : '不限关键词' }}</p>
        <p class="mt-2 text-sm text-slate-500">查询工具：{{ target.tools.map(t => toolNames[t]).join('、') || '全部关闭' }}</p>
        <div class="mt-3 flex flex-wrap gap-2 text-xs"><span class="rounded bg-blue-500/10 px-2 py-1">{{ target.delivery_mode === 'astrbot' ? 'AstrBot 人格播报' : '原文推送' }}</span><span v-if="target.chat_controls" class="rounded bg-blue-500/10 px-2 py-1">聊天管理订阅</span><span v-if="target.reminders_enabled" class="rounded bg-blue-500/10 px-2 py-1">定时提醒</span><span v-if="target.activities_enabled" class="rounded bg-blue-500/10 px-2 py-1">话题与挑战</span></div>
        <div class="mt-4 flex flex-wrap gap-4 text-sm"><button class="text-blue-600" :disabled="!!busy" @click="edit(target)">编辑</button><button :disabled="!!busy" @click="toggle(target)">{{ busy === `toggle-${target.id}` ? '处理中…' : target.enabled ? '暂停' : '启用' }}</button><button class="text-red-500" :disabled="!!busy" @click="remove(target)">删除</button></div>
      </article>
    </div>

    <Teleport to="body">
    <dialog ref="editorDialog" aria-labelledby="session-editor-title" class="session-editor m-auto w-[calc(100%-2rem)] max-w-2xl rounded-2xl border border-slate-200 bg-white p-0 text-slate-900 shadow-2xl dark:border-white/15 dark:bg-zinc-900 dark:text-zinc-100" @cancel.prevent="closeEditor" @click="onDialogClick">
      <div class="flex shrink-0 items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-white/10">
        <h2 id="session-editor-title" class="text-lg font-semibold">{{ form.id ? '编辑会话' : '添加会话' }}</h2>
        <button type="button" aria-label="关闭会话窗口" class="flex h-9 w-9 items-center justify-center rounded-lg text-xl text-slate-500 hover:bg-slate-100 dark:hover:bg-white/10" :disabled="!!busy" @click="closeEditor">×</button>
      </div>
      <form class="flex min-h-0 flex-col" @submit.prevent="saveTarget">
      <div class="grid min-h-0 gap-4 overflow-y-auto overscroll-contain p-5 sm:grid-cols-2">
        <p v-if="editorError" role="alert" class="rounded-lg bg-red-500/10 p-3 text-sm text-red-600 sm:col-span-2 dark:text-red-400">{{ editorError }}</p>
        <label class="text-sm">名称<input v-model.trim="form.name" autofocus required maxlength="100" :class="inputClass" placeholder="我的私聊 / 鸣潮交流群"></label>
        <label class="text-sm">范围<select v-model="form.community" :class="inputClass"><option value="all">全站</option><option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option></select></label>
        <label class="text-sm sm:col-span-2">会话标识（UMO）<input v-model.trim="form.umo" required maxlength="255" :class="inputClass" placeholder="机器人名称:FriendMessage:用户标识 或 机器人名称:GroupMessage:群标识"><span v-if="form.umo" class="mt-1 block text-xs text-slate-500">{{ form.umo.includes(':GroupMessage:') ? '群聊' : '私聊' }} · {{ form.umo.split(':')[0] }} · 尾号 {{ umoTail(form.umo) }}</span><span v-else class="mt-1 block text-xs text-slate-500">在机器人会话发送 /情报会话 获取：QQ 官方平台是一串机器码（不是群号），NapCat/OneBot 平台直接填群号。</span></label>
        <label class="text-sm sm:col-span-2">关键词（留空不限，逗号分隔）<input v-model="keywordInput" :class="inputClass" placeholder="爱弥斯，联名键盘"></label>
        <label class="flex items-center gap-2 text-sm"><input v-model="form.enabled" type="checkbox">启用此会话的推送与查询</label>
        <label class="flex items-center gap-2 text-sm"><input v-model="form.updates_enabled" type="checkbox">推送新采集的官方或重要动态</label>
        <label class="flex items-center gap-2 text-sm"><input v-model="form.daily_enabled" type="checkbox">每日早报<input v-model="form.daily_time" type="time" required class="rounded border bg-transparent px-2 py-1">北京时间</label>
        <div class="flex flex-wrap items-center gap-2 text-sm"><span>即时推送免打扰</span><label><span class="sr-only">开始小时</span><input v-model.number="form.quiet_start" type="number" min="0" max="23" required class="w-14 rounded border bg-transparent px-2 py-1"></label><span>至</span><label><span class="sr-only">结束小时</span><input v-model.number="form.quiet_end" type="number" min="0" max="23" required class="w-14 rounded border bg-transparent px-2 py-1"></label><span>时（相同则关闭）</span></div>
        <fieldset class="sm:col-span-2"><legend class="text-sm font-medium">AI 可用的查询工具</legend><div class="mt-2 flex flex-wrap gap-4"><label v-for="(label, key) in toolNames" :key="key" class="flex items-center gap-2 text-sm"><input v-model="form.tools" type="checkbox" :value="key">{{ label }}</label></div></fieldset>
        <label class="text-sm sm:col-span-2">推送方式<select v-model="form.delivery_mode" :class="inputClass"><option value="astrbot">AstrBot 当前人格播报，可连续追问</option><option value="direct">直接发送原文</option></select></label>
        <label class="flex items-center gap-2 text-sm"><input v-model="form.chat_controls" type="checkbox">允许在聊天中管理订阅</label>
        <label class="flex items-center gap-2 text-sm"><input v-model="form.reminders_enabled" type="checkbox">允许创建当前会话的定时提醒</label>
        <label class="flex items-center gap-2 text-sm sm:col-span-2"><input v-model="form.activities_enabled" type="checkbox">开启「聊点什么」和「情报挑战」（需启用今日速报工具）</label>
        <label v-if="form.chat_controls && form.umo.includes(':GroupMessage:')" class="text-sm sm:col-span-2">可修改全群订阅的用户标识<input v-model="managerInput" :class="inputClass" placeholder="通过 /情报会话 获取管理标识，逗号分隔"></label>
        <label class="text-sm sm:col-span-2">问答模型（AstrBot 中已配置的提供商 ID）<input v-model.trim="form.ai_provider_id" maxlength="200" :class="inputClass" placeholder="留空跟随机器人默认模型，例如 deepseek/deepseek-v4-flash"></label>
        <label class="text-sm sm:col-span-2">该会话的回答偏好<textarea v-model="form.ai_prompt" rows="3" maxlength="2000" :class="inputClass" placeholder="例如：用轻松语气回答；优先解释活动与玩家的关系。"></textarea></label>
      </div>
        <div class="flex shrink-0 justify-end gap-3 border-t border-slate-200 px-5 py-4 dark:border-white/10"><button type="button" class="rounded-lg px-4 py-2 text-sm hover:bg-slate-100 dark:hover:bg-white/10" :disabled="!!busy" @click="closeEditor">取消</button><button class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" :disabled="!!busy">{{ busy === 'target' ? '保存中…' : '保存会话' }}</button></div>
      </form>
    </dialog>
    </Teleport>

    <section v-if="targets.length" class="panel rounded-xl p-5">
      <h2 class="font-semibold">查询与试发</h2>
      <div class="mt-3 grid gap-3 sm:grid-cols-3"><label class="text-sm">接收会话<select v-model="selectedId" :class="inputClass"><option v-for="target in targets" :key="target.id" :value="target.id">{{ target.name }}</option></select></label><label class="text-sm">查询工具<select v-model="tool" :class="inputClass"><option v-for="(label, key) in toolNames" :key="key" :value="key">{{ label }}</option></select></label><label class="text-sm">查询关键词<input v-model.trim="query" maxlength="100" :class="inputClass" placeholder="留空查询所选范围"></label></div>
      <button class="mt-3 rounded-lg border px-3 py-2 text-sm" :disabled="!!busy || !selectedId" @click="preview">{{ busy === 'preview' ? '查询中…' : '生成预览' }}</button>
      <label class="mt-4 block text-sm">待发送内容<textarea v-model="previewText" rows="9" maxlength="3500" :class="inputClass" placeholder="先生成预览，或直接输入测试消息"></textarea></label>
      <div class="mt-3 flex flex-wrap items-center gap-3"><button class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" :disabled="!!busy || !selectedId || !previewText.trim()" @click="send">{{ busy === 'send' ? '发送中…' : `发送给「${selected?.name || ''}」` }}</button><span class="text-xs text-slate-500">人格播报会进入发送队列，也可向已暂停的会话试发。</span></div>
    </section>

    <section class="panel rounded-xl p-5">
      <h2 class="font-semibold">会话提醒</h2>
      <p v-if="!companion.reminders.length" class="py-5 text-sm text-slate-500">在已开启提醒的会话中说「明天上午 8 点提醒我查看公告」即可创建。</p>
      <div v-for="row in companion.reminders" :key="row.id" class="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4 text-sm dark:border-white/10"><div class="min-w-0 flex-1"><p class="font-medium">#{{ row.id }} · {{ row.name }} · {{ fmtTime(row.due_at) }}</p><p class="mt-1 break-words text-slate-500">{{ row.body }}</p></div><span>{{ { pending: '待提醒', queued: '已到期，见投递记录', cancelled: '已取消' }[row.status] || row.status }}</span><button v-if="['pending', 'queued'].includes(row.status)" class="text-blue-600" :disabled="!!busy" @click="cancelReminder(row)">{{ busy === `reminder-${row.id}` ? '取消中…' : '取消提醒' }}</button></div>
    </section>

    <section class="panel rounded-xl p-5">
      <h2 class="font-semibold">最近投递</h2>
      <p v-if="!deliveries.length" class="py-5 text-sm text-slate-500">暂无投递记录</p>
      <div class="mt-3 divide-y divide-slate-200 dark:divide-white/10"><details v-for="row in deliveries" :key="row.id" class="py-3"><summary class="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 text-sm"><span class="font-medium">{{ row.name }}</span><span>{{ { daily: '每日早报', updates: '重要动态', manual: '手动发送', reminder: '定时提醒' }[row.kind] || row.kind }}</span><span :class="row.status === 'accepted' ? 'text-emerald-600' : ['failed', 'unknown'].includes(row.status) ? 'text-amber-600' : 'text-slate-500'">{{ statuses[row.status] || row.status }}</span><span class="ml-auto text-xs text-slate-500">{{ fmtTime(row.created_at) }}</span></summary><p v-if="row.error" class="mt-2 text-sm text-amber-600">{{ row.error }}</p><pre class="mt-3 whitespace-pre-wrap break-words text-sm text-slate-600 dark:text-zinc-300">{{ row.rendered_body || row.body }}</pre><details v-if="row.rendered_body && row.rendered_body !== row.body" class="mt-3 text-sm"><summary class="cursor-pointer text-slate-500">原始资料</summary><pre class="mt-2 whitespace-pre-wrap break-words">{{ row.body }}</pre></details><button v-if="['failed', 'unknown'].includes(row.status)" class="mt-3 text-sm text-blue-600" :disabled="!!busy" @click="retry(row)">{{ busy === `retry-${row.id}` ? '重试中…' : '核对后重试' }}</button></details></div>
    </section>
  </div>
</template>

<style scoped>
.session-editor[open] {
  display: flex;
  flex-direction: column;
  max-height: calc(100dvh - 2rem);
  overflow: hidden;
}
.session-editor::backdrop {
  background: rgb(15 23 42 / 45%);
  backdrop-filter: blur(4px);
}
</style>
