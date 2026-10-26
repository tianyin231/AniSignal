<script setup lang="ts">
import { ADMIN_COMMUNITIES, ADMIN_COMMUNITY_LABELS } from '~/utils/admin-communities'
import { fmtTime } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '机器人通知 · AniSignal' })
const { api } = useAdminApi()
const toast = useAdminToast()
interface Target {
  id: number; name: string; umo: string; enabled: boolean; communities: string[]; community: string; keywords: string[]; channels: string[]
  client_id: string; client_name: string; managed_by_plugin: boolean; plugin_configured: boolean; last_activity?: string
  daily_enabled: boolean; daily_time: string; updates_enabled: boolean; min_push_interval_minutes: number
  calendar_enabled: boolean; calendar_time: string; tools: string[]; delivery_mode: string
}
interface BotClient { id: string; name: string; enabled: boolean; online: boolean; version: string; last_seen?: string }
interface Delivery { id: number; target_id: number; name: string; kind: string; status: string; error: string | null; body: string; rendered_body?: string; created_at: string }
interface Reminder { id: number; name: string; body: string; due_at: string; status: string }
const clients = ref<BotClient[]>([])
const channelLabels = ref<Record<string, string>>({})
const targets = ref<Target[]>([])
const deliveries = ref<Delivery[]>([])
const companion = ref<{ counts: Record<string, number>; reminders: Reminder[] }>({ counts: {}, reminders: [] })
const clientName = ref('')
const issuedKey = ref('')
const issuedFor = ref('')
const keyVisible = ref(false)
const busy = ref('')
const loading = ref(true)
const loadError = ref('')
const selectedId = ref<number | null>(null)
const tool = ref('digest')
const query = ref('')
const previewText = ref('')
const selected = computed(() => targets.value.find(t => t.id === selectedId.value))
const availableTargets = computed(() => targets.value.filter(t => t.plugin_configured))
const activeCount = computed(() => targets.value.filter(t => t.enabled).length)
const onlineCount = computed(() => clients.value.filter(c => c.online).length)
const inputClass = 'mt-1 w-full rounded-lg border border-line bg-card px-3 py-2 text-sm'
const toolNames: Record<string, string> = { digest: '今日速报', news: '资讯查询', products: '商品进展', trending: '热度飙升', calendar: '活动日历', codes: '兑换码' }
const statuses: Record<string, string> = { pending: '待发送', sending: '发送中', accepted: '已接收', failed: '发送失败', unknown: '结果待核对', cancelled: '已取消' }

function subscriptionLabel(target: Target) {
  if (target.channels?.length) return target.channels.map(key => channelLabels.value[key] || key).join(' / ')
  const communities = target.communities || (target.community === 'all' ? ADMIN_COMMUNITIES.map(c => c.key) : [target.community])
  return communities.length === ADMIN_COMMUNITIES.length ? '全站' : communities.map(key => ADMIN_COMMUNITY_LABELS[key] || key).join(' / ')
}
function relTime(iso?: string) {
  if (!iso) return '暂无记录'
  const ts = new Date(/[z+]/i.test(iso) ? iso : `${iso}Z`).getTime()
  const minutes = Math.floor((Date.now() - ts) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`
  return fmtTime(iso)
}
async function refresh() {
  loading.value = true
  loadError.value = ''
  try {
    const [data, logs, state] = await Promise.all([
      api<{ clients: BotClient[]; targets: Target[]; channels: Record<string, string> }>('/api/admin/bot'),
      api<Delivery[]>('/api/admin/bot/deliveries'),
      api<typeof companion.value>('/api/admin/bot/companion'),
    ])
    clients.value = data.clients
    targets.value = data.targets
    channelLabels.value = data.channels || {}
    deliveries.value = logs
    companion.value = state
    if (!availableTargets.value.some(t => t.id === selectedId.value)) selectedId.value = availableTargets.value[0]?.id ?? null
  } catch (e: any) { loadError.value = e?.data?.detail || e.message }
  finally { loading.value = false }
}
onMounted(refresh)
watch([selectedId, tool, query], () => { previewText.value = '' })
async function action(key: string, fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = key
  try { await fn() }
  catch (e: any) { toast.err(typeof e?.data?.detail === 'string' ? e.data.detail : '操作失败，请检查网络与输入') }
  finally { busy.value = '' }
}
function showKey(name: string, key: string) {
  issuedFor.value = name
  issuedKey.value = key
  keyVisible.value = false
}
function createClient() {
  action('client-create', async () => {
    const result = await api<{ id: string; key: string }>('/api/admin/bot/clients', { method: 'POST', body: { name: clientName.value } })
    showKey(clientName.value, result.key)
    clientName.value = ''
    await refresh()
  })
}
function rotateClient(client: BotClient) {
  if (!confirm(`重置「${client.name}」的密钥？原密钥立即失效，需要在 AstrBot 插件设置中填写新密钥。`)) return
  action(`rotate-${client.id}`, async () => {
    const result = await api<{ key: string }>(`/api/admin/bot/clients/${client.id}/rotate`, { method: 'POST' })
    showKey(client.name, result.key)
    await refresh()
  })
}
function toggleClient(client: BotClient) {
  if (client.enabled && !confirm(`停用「${client.name}」接入并暂停它的会话？`)) return
  action(`client-${client.id}`, async () => {
    await api(`/api/admin/bot/clients/${client.id}`, { method: 'PUT', body: { name: client.name, enabled: !client.enabled } })
    toast.ok(client.enabled ? '接入已停用' : '接入已启用，等待插件同步会话')
    await refresh()
  })
}
function preview() {
  action('preview', async () => {
    const result = await api<{ text: string }>(`/api/admin/bot/targets/${selectedId.value}/preview`, { method: 'POST', body: { tool: tool.value, query: query.value } })
    previewText.value = result.text
    toast.ok('预览已生成')
  })
}
function send() {
  action('send', async () => {
    const result = await api<{ status: string; error: string }>(`/api/admin/bot/targets/${selectedId.value}/send`, { method: 'POST', body: { content: previewText.value } })
    if (result.status === 'accepted') toast.ok('已接收，请在目标会话核对消息')
    else if (result.status === 'pending') toast.ok('已排队，等待 AstrBot 接收')
    else toast.err(result.error || '发送失败')
    await refresh()
  })
}
function retry(row: Delivery) {
  if (!confirm('请先确认原消息没有送达，再重试。继续？')) return
  action(`retry-${row.id}`, async () => {
    const result = await api<{ status: string; error?: string }>(`/api/admin/bot/deliveries/${row.id}/retry`, { method: 'POST' })
    if (['accepted', 'pending'].includes(result.status)) toast.ok('已重新排队')
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
    <div class="wall-heading flex flex-wrap items-center justify-between gap-3">
      <div><h1 class="text-2xl font-bold text-ink">机器人通知</h1><p class="mt-1 text-sm text-muted">{{ targets.length }} 个会话 · {{ activeCount }} 个已启用</p></div>
      <button class="min-h-11 rounded-lg border border-line px-3 text-sm" :disabled="loading || !!busy" @click="refresh">{{ loading ? '加载中…' : '刷新' }}</button>
    </div>
    <p v-if="loadError" role="alert" class="rounded-lg bg-red-500/10 p-3 text-red-600">{{ loadError }}</p>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div class="panel p-4"><p class="text-sm text-muted">AstrBot 已连接</p><p class="mt-2 text-xl font-semibold">{{ onlineCount }} / {{ clients.length }}</p></div>
      <div class="panel p-4"><p class="text-sm text-muted">近 24 小时已接收</p><p class="mt-2 text-xl font-semibold">{{ companion.counts.accepted || 0 }}</p></div>
      <div class="panel p-4"><p class="text-sm text-muted">近 24 小时待处理</p><p class="mt-2 text-xl font-semibold">{{ (companion.counts.pending || 0) + (companion.counts.sending || 0) }}</p></div>
      <div class="panel p-4"><p class="text-sm text-muted">近 24 小时需核对</p><p class="mt-2 text-xl font-semibold">{{ (companion.counts.failed || 0) + (companion.counts.unknown || 0) }}</p></div>
    </div>

    <section class="panel p-5" aria-labelledby="bot-clients-title">
      <h2 id="bot-clients-title" class="font-semibold">AstrBot 接入</h2>
      <p class="mt-2 text-sm text-muted">订阅分区、早报、免打扰与工具权限在 AstrBot → 插件 → AniSignal → 插件配置中维护。</p>
      <form class="mt-4 flex flex-wrap items-end gap-3" @submit.prevent="createClient">
        <label class="min-w-0 flex-1 text-sm">接入名称<input v-model.trim="clientName" required maxlength="100" :class="inputClass" placeholder="例如：我的 AstrBot"></label>
        <button class="min-h-11 rounded-lg bg-brand px-4 text-sm text-white disabled:opacity-50" :disabled="!!busy">{{ busy === 'client-create' ? '创建中…' : '创建独立接入密钥' }}</button>
      </form>
      <div v-if="issuedKey" class="mt-4 rounded-xl border border-line bg-chip p-4">
        <p class="text-sm font-semibold">{{ issuedFor }} · 接入密钥</p>
        <p class="mt-1 text-xs text-muted">密钥仅展示这一次。复制到 AstrBot 的 AniSignal 插件设置中。</p>
        <input :value="issuedKey" :type="keyVisible ? 'text' : 'password'" readonly aria-label="新接入密钥" :class="inputClass" @focus="($event.target as HTMLInputElement).select()">
        <div class="mt-2 flex gap-4"><button class="min-h-11 text-sm text-brand-ink" @click="keyVisible = !keyVisible">{{ keyVisible ? '隐藏密钥' : '显示密钥' }}</button><button class="min-h-11 text-sm" @click="issuedKey = ''">关闭</button></div>
      </div>
      <div v-if="!clients.length && !loading" class="mt-5 text-sm text-muted">创建接入后，将服务地址和密钥填写到 AstrBot 插件设置中。</div>
      <div v-for="client in clients" :key="client.id" class="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4">
        <div class="min-w-0 flex-1"><h3 class="font-semibold">{{ client.name }}</h3><p class="mt-1 text-xs text-muted">{{ targets.filter(t => t.client_id === client.id).length }} 个会话 · {{ client.version || '等待连接' }} · 最近连接 {{ relTime(client.last_seen) }}</p></div>
        <span class="text-sm" :class="client.online ? 'text-ok' : 'text-muted'">{{ !client.enabled ? '已停用' : client.online ? '在线' : '未连接' }}</span>
        <button class="min-h-11 text-sm text-brand-ink" :disabled="!!busy" @click="rotateClient(client)">重置密钥</button>
        <button class="min-h-11 text-sm" :disabled="!!busy" @click="toggleClient(client)">{{ client.enabled ? '停用接入' : '启用接入' }}</button>
      </div>
    </section>

    <section aria-labelledby="bot-targets-title">
      <div class="mb-3 flex flex-wrap items-baseline justify-between gap-2"><h2 id="bot-targets-title" class="font-semibold">会话订阅</h2><p class="text-xs text-muted">由 AstrBot 插件设置同步</p></div>
      <div v-if="!targets.length && !loading" class="panel p-8 text-center text-sm text-muted">连接插件后，在插件配置中添加群聊或私聊。</div>
      <div class="grid gap-4 sm:grid-cols-2">
        <article v-for="target in targets" :key="target.id" class="panel min-w-0 p-5">
          <div class="flex items-start justify-between gap-3"><div class="min-w-0"><h3 class="font-semibold">{{ target.name }}</h3><p class="mt-1 text-sm text-muted">{{ target.client_name }} · {{ target.umo.includes(':GroupMessage:') ? '群聊' : '私聊' }} · {{ subscriptionLabel(target) }}</p></div><span class="shrink-0 rounded-full px-2 py-1 text-xs" :class="target.enabled ? 'bg-ok/10 text-ok' : 'bg-chip text-muted'">{{ !target.plugin_configured ? '已移除' : target.enabled ? '已启用' : '已暂停' }}</span></div>
          <p class="mono mt-2 truncate text-xs text-muted" :title="target.umo">{{ target.umo.split(':')[0] }} · 尾号 {{ (target.umo.split(':')[2] || '').slice(-8) }}</p>
          <p class="mt-3 text-sm">{{ target.daily_enabled ? `每日 ${target.daily_time} 早报` : '早报关闭' }} · {{ target.updates_enabled ? '动态推送开启' : '动态推送关闭' }}</p>
          <p v-if="target.updates_enabled" class="mt-1 text-xs text-muted">自动消息间隔 {{ target.min_push_interval_minutes }} 分钟 · {{ target.calendar_enabled ? `收尾提醒 ${target.calendar_time}` : '收尾提醒关闭' }}</p>
          <p class="mt-2 text-sm text-muted">{{ target.keywords.length ? target.keywords.join(' / ') : '不限关键词' }}</p>
          <p class="mt-2 text-sm text-muted">查询工具：{{ target.tools.map(t => toolNames[t]).join('、') || '全部关闭' }}</p>
          <p class="mt-3 text-xs text-faint">{{ target.managed_by_plugin ? '配置来源：AstrBot 插件' : target.delivery_mode === 'astrbot' ? '等待插件接管已有配置' : '旧版直发通道' }} · 最近活跃 {{ relTime(target.last_activity) }}</p>
        </article>
      </div>
    </section>

    <details v-if="availableTargets.length" class="panel p-5">
      <summary class="cursor-pointer font-semibold">查询与试发</summary>
      <div class="mt-3 grid gap-3 sm:grid-cols-3"><label class="text-sm">接收会话<select v-model="selectedId" :class="inputClass"><option v-for="target in availableTargets" :key="target.id" :value="target.id">{{ target.client_name }} · {{ target.name }}</option></select></label><label class="text-sm">查询工具<select v-model="tool" :class="inputClass"><option v-for="(label, key) in toolNames" :key="key" :value="key">{{ label }}</option></select></label><label class="text-sm">查询关键词<input v-model.trim="query" maxlength="100" :class="inputClass"></label></div>
      <button class="mt-3 min-h-11 rounded-lg border border-line px-3 text-sm" :disabled="!!busy || !selectedId" @click="preview">生成预览</button>
      <label class="mt-4 block text-sm">待发送内容<textarea v-model="previewText" rows="7" maxlength="3500" :class="inputClass"></textarea></label>
      <button class="mt-3 min-h-11 rounded-lg bg-brand px-4 text-sm text-white" :disabled="!!busy || !selectedId || !previewText.trim()" @click="send">{{ busy === 'send' ? '发送中…' : `发送给「${selected?.name || ''}」` }}</button>
    </details>

    <section class="panel p-5">
      <h2 class="font-semibold">会话提醒</h2>
      <p v-if="!companion.reminders.length" class="py-5 text-sm text-muted">暂无会话提醒。</p>
      <div v-for="row in companion.reminders" :key="row.id" class="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4 text-sm"><div class="min-w-0 flex-1"><p class="font-medium">#{{ row.id }} · {{ row.name }} · {{ fmtTime(row.due_at) }}</p><p class="mt-1 break-words text-muted">{{ row.body }}</p></div><span>{{ { pending: '待提醒', queued: '已到期，见投递记录', cancelled: '已取消' }[row.status] || row.status }}</span><button v-if="['pending', 'queued'].includes(row.status)" class="min-h-11 text-brand-ink" :disabled="!!busy" @click="cancelReminder(row)">取消提醒</button></div>
    </section>

    <section class="panel p-5">
      <h2 class="font-semibold">最近投递</h2>
      <p v-if="!deliveries.length" class="py-5 text-sm text-muted">暂无投递记录</p>
      <div class="mt-3 divide-y divide-line"><details v-for="row in deliveries" :key="row.id" class="py-3"><summary class="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 text-sm"><span class="font-medium">{{ row.name }}</span><span>{{ { daily: '每日早报', updates: '重要动态', critical: '关键动态', manual: '手动发送', reminder: '定时提醒', calendar: '日历提醒' }[row.kind] || row.kind }}</span><span :class="row.status === 'accepted' ? 'text-ok' : ['failed', 'unknown'].includes(row.status) ? 'text-warn' : 'text-muted'">{{ statuses[row.status] || row.status }}</span><span class="ml-auto text-xs text-muted">{{ fmtTime(row.created_at) }}</span></summary><p v-if="row.error" class="mt-2 text-sm text-warn">{{ row.error }}</p><pre class="mt-3 whitespace-pre-wrap break-words text-sm text-ink-2">{{ row.rendered_body || row.body }}</pre><details v-if="row.rendered_body && row.rendered_body !== row.body" class="mt-3 text-sm"><summary class="cursor-pointer text-muted">原始资料</summary><pre class="mt-2 whitespace-pre-wrap break-words">{{ row.body }}</pre></details><button v-if="['failed', 'unknown'].includes(row.status)" class="mt-3 min-h-11 text-sm text-brand-ink" :disabled="!!busy" @click="retry(row)">核对后重试</button></details></div>
    </section>
  </div>
</template>
