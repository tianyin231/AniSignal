<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '采集代理 · AniSignal 管理看板' })

interface TestResult { ok: boolean; message: string; checked_at: string; latency_ms: number; exit_ip?: string }
interface ProxyEntry {
  id: string; label: string; scheme: string; host: string; port: number; username: string
  enabled: boolean; password_set: boolean; last_test?: TestResult
}
interface ProxyPool { enabled: boolean; platforms: Record<string, boolean>; entries: ProxyEntry[] }
interface ImportResult { added: number; duplicates: number; errors: { line: number; message: string }[]; pool: ProxyPool }

const { api } = useAdminApi()
const toast = useAdminToast()
const pool = ref<ProxyPool>({ enabled: false, platforms: {}, entries: [] })
const enabled = ref(false)
const platforms = ref<Record<string, boolean>>({})
const loaded = ref(false)
const busy = ref('')
const error = ref('')
const mode = ref<'single' | 'batch'>('single')
const scheme = ref('http')
const input = ref('')
const label = ref('')
const imported = ref<ImportResult | null>(null)
const editing = ref<string | null>(null)
const edit = reactive({ label: '', scheme: 'http', host: '', port: 80, username: '', password: '', clearPassword: false })
const names: Record<string, string> = {
  bilibili: 'B 站（含定制监控）', x: 'X', youtube: 'YouTube / RSS', miyoushe: '米游社',
  kuro_cn: '库洛国服官网', kuro_global: '库洛国际官网', aihot: 'AIHOT（RSS / 网页 / 正文）',
}
const activeCount = computed(() => pool.value.entries.filter(entry => entry.enabled).length)

function apply(value: ProxyPool) {
  pool.value = value
  enabled.value = value.enabled
  platforms.value = { ...value.platforms }
  loaded.value = true
}

async function perform(key: string, action: () => Promise<void>) {
  if (busy.value) return
  busy.value = key
  error.value = ''
  try { await action() }
  catch (exc: any) {
    const detail = exc?.data?.detail
    error.value = typeof detail === 'string' ? detail : '操作失败，请检查输入或稍后重试'
    toast.err(error.value)
  }
  finally { busy.value = '' }
}

async function refresh() {
  await perform('load', async () => { apply(await api<ProxyPool>('/api/admin/proxies')) })
}
onMounted(refresh)

async function saveOptions() {
  await perform('options', async () => {
    apply(await api<ProxyPool>('/api/admin/proxies', { method: 'PUT', body: { enabled: enabled.value, platforms: platforms.value } }))
    toast.ok('采集代理配置已保存，下一次采集生效')
  })
}

async function add() {
  await perform('import', async () => {
    const result = await api<ImportResult>('/api/admin/proxies', {
      method: 'POST', body: { text: input.value, scheme: scheme.value, label: mode.value === 'single' ? label.value : '' },
    })
    imported.value = result
    apply(result.pool)
    if (result.added) { input.value = ''; label.value = ''; toast.ok(`已添加 ${result.added} 个代理`) }
    else if (result.errors.length) toast.err('导入包含无效格式，请检查行号')
    else toast.ok('代理已存在，已跳过重复项')
  })
}

async function readFile(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  await perform('file', async () => { input.value = await file.text(); mode.value = 'batch'; imported.value = null })
  target.value = ''
}

function startEdit(entry: ProxyEntry) {
  editing.value = entry.id
  Object.assign(edit, { label: entry.label || '', scheme: entry.scheme, host: entry.host, port: entry.port,
    username: entry.username || '', password: '', clearPassword: false })
}

async function saveEntry() {
  if (!editing.value) return
  await perform('edit', async () => {
    const body: Record<string, unknown> = { label: edit.label, scheme: edit.scheme, host: edit.host, port: edit.port, username: edit.username }
    if (edit.clearPassword) body.password = ''
    else if (edit.password) body.password = edit.password
    apply(await api<ProxyPool>(`/api/admin/proxies/${editing.value}`, { method: 'PATCH', body }))
    editing.value = null
    edit.password = ''
    toast.ok('代理已更新')
  })
}

async function toggle(entry: ProxyEntry) {
  await perform(entry.id, async () => {
    apply(await api<ProxyPool>(`/api/admin/proxies/${entry.id}`, { method: 'PATCH', body: { enabled: !entry.enabled } }))
    toast.ok(entry.enabled ? '代理已停用' : '代理已启用')
  })
}

async function test(entry: ProxyEntry) {
  await perform(`test:${entry.id}`, async () => {
    entry.last_test = await api<TestResult>(`/api/admin/proxies/${entry.id}/test`, { method: 'POST' })
    entry.last_test.ok ? toast.ok('代理连接正常') : toast.err(entry.last_test.message)
  })
}

async function remove(entry: ProxyEntry) {
  if (!confirm(`删除代理「${entry.label || `${entry.host}:${entry.port}`}」？`)) return
  await perform(entry.id, async () => {
    apply(await api<ProxyPool>(`/api/admin/proxies/${entry.id}`, { method: 'DELETE' }))
    if (editing.value === entry.id) editing.value = null
    toast.ok('代理已删除')
  })
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-lg font-semibold">采集代理</h1>
      <div class="flex items-center gap-3 text-sm">
        <span class="text-muted">{{ activeCount }} / {{ pool.entries.length }} 个启用</span>
        <span class="rounded-full px-3 py-1 text-xs font-semibold" :class="pool.enabled ? 'bg-ok/10 text-ok' : 'bg-chip text-muted'">{{ pool.enabled ? '代理池已启用' : '当前直连' }}</span>
        <button type="button" class="rounded-lg border border-line px-3 py-1.5 disabled:opacity-50" :disabled="!!busy" @click="refresh">{{ busy === 'load' ? '加载中…' : '刷新' }}</button>
      </div>
    </div>
    <p v-if="error" role="alert" class="panel border-red-300/50 p-4 text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    <form class="panel p-5" @submit.prevent="saveOptions">
      <fieldset :disabled="!!busy || !loaded" class="space-y-4 disabled:opacity-60">
        <label class="flex items-center gap-2 font-semibold"><input v-model="enabled" type="checkbox">启用采集代理池</label>
        <div class="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <label v-for="(name, key) in names" :key="key" class="flex items-center gap-2"><input v-model="platforms[key]" type="checkbox">{{ name }}</label>
        </div>
        <button class="btn-primary px-4 py-2" type="submit">{{ busy === 'options' ? '保存中…' : '保存配置' }}</button>
      </fieldset>
    </form>
    <form class="panel p-5" @submit.prevent="add">
      <fieldset :disabled="!!busy || !loaded" class="space-y-4 disabled:opacity-60">
        <div class="flex flex-wrap items-center gap-2">
          <button v-for="tab in (['single', 'batch'] as const)" :key="tab" type="button" class="rounded-lg px-3 py-2 text-sm font-semibold" :class="mode === tab ? 'bg-ink text-card' : 'bg-chip text-ink-2'" :aria-pressed="mode === tab" @click="mode = tab; imported = null">{{ tab === 'single' ? '单个添加' : '批量导入' }}</button>
          <label for="proxy-scheme" class="ml-auto text-sm text-muted">默认协议</label>
          <select id="proxy-scheme" v-model="scheme" class="select"><option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5">SOCKS5</option></select>
        </div>
        <div v-if="mode === 'single'" class="grid gap-3 sm:grid-cols-[1fr_2fr]">
          <label class="space-y-1 text-sm"><span>名称（可选）</span><input v-model="label" class="input w-full" placeholder="如 美国出口 1" maxlength="120"></label>
          <label class="space-y-1 text-sm"><span>代理地址</span><input v-model="input" class="input w-full font-mono" placeholder="host:port:username:password" required autocomplete="off" spellcheck="false"></label>
        </div>
        <div v-else class="space-y-3">
          <label class="block space-y-1 text-sm"><span>代理列表（每行一个）</span><textarea v-model="input" class="textarea w-full font-mono" rows="6" placeholder="host:port:username:password&#10;http://username:password@host:port&#10;socks5://username:password@host:port" required spellcheck="false" /></label>
          <label class="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm">选择 TXT / CSV 文件<input class="sr-only" type="file" accept=".txt,.csv,text/plain,text/csv" @change="readFile"></label>
        </div>
        <p class="text-xs leading-relaxed text-muted">支持 Webshare 四段格式、代理 URL、用户名:密码@地址:端口、地址:端口，以及空格 / 逗号分隔的四列。无协议时使用上方默认协议。</p>
        <button class="btn-primary px-4 py-2" type="submit">{{ busy === 'import' ? '导入中…' : mode === 'single' ? '添加代理' : '批量导入' }}</button>
      </fieldset>
      <div v-if="imported" class="mt-4 space-y-2 text-sm" role="status">
        <p>新增 {{ imported.added }} 个 · 重复 {{ imported.duplicates }} 个 · 无效 {{ imported.errors.length }} 行</p>
        <ul v-if="imported.errors.length" class="max-h-36 overflow-auto text-red-600 dark:text-red-400"><li v-for="item in imported.errors" :key="item.line">第 {{ item.line }} 行：{{ item.message }}</li></ul>
      </div>
    </form>
    <form v-if="editing" class="panel border-brand/40 p-5" @submit.prevent="saveEntry">
      <fieldset :disabled="!!busy" class="space-y-4 disabled:opacity-60">
        <h2 class="font-semibold">编辑代理</h2>
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <label class="space-y-1 text-sm"><span>名称</span><input v-model="edit.label" class="input w-full" maxlength="120"></label>
          <label class="space-y-1 text-sm"><span>协议</span><select v-model="edit.scheme" class="select w-full"><option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5">SOCKS5</option></select></label>
          <label class="space-y-1 text-sm"><span>地址</span><input v-model="edit.host" class="input w-full" required></label>
          <label class="space-y-1 text-sm"><span>端口</span><input v-model.number="edit.port" class="input w-full" type="number" min="1" max="65535" required></label>
          <label class="space-y-1 text-sm"><span>用户名</span><input v-model="edit.username" class="input w-full" autocomplete="off"></label>
          <label class="space-y-1 text-sm"><span>密码</span><input v-model="edit.password" class="input w-full" type="password" autocomplete="new-password" placeholder="留空保留已存密码" :disabled="edit.clearPassword"></label>
        </div>
        <label class="flex items-center gap-2 text-sm"><input v-model="edit.clearPassword" type="checkbox">清除已存密码</label>
        <div class="flex gap-2"><button class="btn-primary px-4 py-2">{{ busy === 'edit' ? '保存中…' : '保存代理' }}</button><button type="button" class="rounded-lg border border-line px-4 py-2 text-sm" @click="editing = null; edit.password = ''">取消</button></div>
      </fieldset>
    </form>
    <section class="panel overflow-hidden">
      <h2 class="border-b border-line px-5 py-4 font-semibold">代理池</h2>
      <p v-if="!pool.entries.length" class="p-8 text-center text-sm text-muted">{{ loaded ? '还没有代理，添加后即可启用。' : '正在加载代理…' }}</p>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="bg-chip text-xs text-muted"><tr><th class="px-5 py-3">名称 / 地址</th><th class="px-3 py-3">协议 / 认证</th><th class="px-3 py-3">状态</th><th class="px-3 py-3">最近测试</th><th class="px-5 py-3">操作</th></tr></thead>
          <tbody class="divide-y divide-line">
            <tr v-for="entry in pool.entries" :key="entry.id">
              <td class="px-5 py-4"><span v-if="entry.label" class="block font-medium">{{ entry.label }}</span><span class="whitespace-nowrap font-mono text-xs">{{ entry.host }}:{{ entry.port }}</span></td>
              <td class="px-3 py-4"><span class="block uppercase">{{ entry.scheme }}</span><span class="text-xs text-muted">{{ entry.username || '无认证' }}{{ entry.password_set ? ' · 已设密码' : '' }}</span></td>
              <td class="whitespace-nowrap px-3 py-4"><button :disabled="!!busy" class="rounded-full px-2 py-1 text-xs disabled:opacity-50" :class="entry.enabled ? 'bg-ok/10 text-ok' : 'bg-chip text-muted'" @click="toggle(entry)">{{ busy === entry.id ? '处理中…' : entry.enabled ? '已启用' : '已停用' }}</button></td>
              <td class="px-3 py-4"><template v-if="entry.last_test"><span :class="entry.last_test.ok ? 'text-ok' : 'text-red-600 dark:text-red-400'">{{ entry.last_test.ok ? `${entry.last_test.latency_ms} ms · ${entry.last_test.exit_ip}` : entry.last_test.message }}</span><span class="block whitespace-nowrap text-xs text-muted">{{ fmtTime(entry.last_test.checked_at) }}</span></template><span v-else class="text-muted">未测试</span></td>
              <td class="px-5 py-4"><div class="flex flex-nowrap gap-3 whitespace-nowrap"><button class="text-brand-ink disabled:opacity-50" :disabled="!!busy" @click="test(entry)">{{ busy === `test:${entry.id}` ? '测试中…' : '测试连接' }}</button><button class="text-brand-ink disabled:opacity-50" :disabled="!!busy" @click="startEdit(entry)">编辑</button><button class="text-red-600 disabled:opacity-50 dark:text-red-400" :disabled="!!busy" @click="remove(entry)">删除</button></div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
