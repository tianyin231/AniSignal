<script setup lang="ts">
import { Bot, Coins, Cpu, Zap } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: 'AI 层 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()
const busy = ref(false)
const busyWhat = ref('')

// ---------- 配置 ----------
interface AiConfig {
  base_url: string
  model: string
  api_key_set: boolean
  api_keys_count?: number
  api_keys_tail?: string[]
  price_in: number
  price_out: number
}
const config = reactive({ base_url: '', model: '', api_key: '', api_keys: '', price_in: 0, price_out: 0 })
const configView = ref<AiConfig | null>(null)
const testResult = ref('')

async function loadConfig() {
  configView.value = await api<AiConfig>('/api/admin/ai/config')
  config.base_url = configView.value.base_url
  config.model = configView.value.model
  config.price_in = configView.value.price_in
  config.price_out = configView.value.price_out
  config.api_key = ''
}

// ---------- 用量 ----------
interface UsageWindow { calls: number; total_tokens: number; cost: number }
interface TaskUsage { task: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; cost: number; avg_ms: number }
interface UsageSummary { window_days: number; window: UsageWindow; all_time: UsageWindow; by_task: TaskUsage[] }
interface CallRow { id: number; task: string; ref_id: number | null; model: string; prompt_tokens: number; completion_tokens: number; total_tokens: number; cost: number; duration_ms: number; status: string; created_at: string }
const usage = ref<UsageSummary | null>(null)
const calls = ref<CallRow[]>([])
const days = ref(7)

async function loadUsage() {
  const [u, c] = await Promise.all([
    api<UsageSummary>('/api/admin/ai/usage', { query: { days: days.value } }),
    api<CallRow[]>('/api/admin/ai/calls', { query: { limit: 30 } }),
  ])
  usage.value = u
  calls.value = c
}

async function refresh() {
  await Promise.all([loadConfig(), loadUsage()])
}
onMounted(() => refresh())

const TASK_LABELS: Record<string, string> = {
  summarize_event: '事件摘要',
  translate_content: '内容翻译',
  assess_product: '商品履约判断',
  test: '连通测试',
}

async function saveConfig() {
  busy.value = true; busyWhat.value = 'save'
  try {
    await api('/api/admin/ai/config', {
      method: 'PUT',
      body: { ...config, api_keys: config.api_keys.trim() ? config.api_keys : null },
    })
    config.api_key = ''
    toast.ok('LLM 配置已保存')
    await loadConfig()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? '保存失败')
  } finally { busy.value = false; busyWhat.value = '' }
}

async function runTest() {
  if (busy.value) return
  busy.value = true; busyWhat.value = 'test'
  testResult.value = ''
  try {
    const r = await api<{ ok: boolean; message: string }>('/api/admin/ai/test', { method: 'POST' })
    testResult.value = `${new Date().toLocaleTimeString('zh-CN', { hour12: false })} · ${r.message}`
    r.ok ? toast.ok('LLM 连通正常') : toast.err('LLM 连通失败')
    await loadUsage()
  } catch (exc: any) {
    testResult.value = exc?.data?.detail ?? '测试失败'
    toast.err('测试请求失败')
  } finally { busy.value = false; busyWhat.value = '' }
}

async function summarizeAll() {
  if (!confirm('为缺少摘要或使用降级模板的事件补 AI 摘要？人工编辑和锁定的事件会保留。')) return
  busy.value = true; busyWhat.value = 'summarize'
  toast.info('正在提交 AI 摘要任务…')
  try {
    const r = await api<{ candidates: number; queued: number; remaining: number }>('/api/admin/ai/summarize-all', { method: 'POST' })
    toast.info(r.queued ? `已新增 ${r.queued} 条摘要任务，后台处理中；当前待补 ${r.remaining} 条（含队列中）` : r.remaining ? '本批摘要任务已在队列中，请稍后查看调用记录' : '无需补充摘要')
    await loadUsage()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? '执行失败')
  } finally { busy.value = false; busyWhat.value = '' }
}

async function translateMissing() {
  if (!confirm('为外语内容（国际服公告 / YouTube）补机翻？')) return
  busy.value = true; busyWhat.value = 'translate'
  toast.info('正在提交翻译任务…')
  try {
    const r = await api<{ candidates: number; queued: number; remaining: number }>('/api/admin/ai/translate-missing', { method: 'POST' })
    toast.info(r.queued ? `已新增 ${r.queued} 条翻译任务，后台处理中；当前待翻 ${r.remaining} 条（含队列中）` : r.remaining ? '本批翻译任务已在队列中，请稍后查看调用记录' : '无需补充翻译')
    await loadUsage()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? '执行失败')
  } finally { busy.value = false; busyWhat.value = '' }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-lg font-semibold">AI 层（LLM）</h1>
      <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
        配置 OpenAI 兼容接口，管理事件摘要与内容翻译任务，跟踪 token 消耗与费用。
      </p>
    </div>

    <!-- 状态卡 -->
    <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <div class="panel px-4 py-3">
        <p class="flex items-center gap-1.5 text-xs text-slate-500 dark:text-zinc-400"><Bot :size="13" /> 模型</p>
        <p class="mt-1 truncate text-sm font-semibold">{{ configView?.model || '未配置' }}</p>
        <p class="mono mt-0.5 truncate text-[11px] text-slate-400">{{ configView?.base_url || '—' }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="flex items-center gap-1.5 text-xs text-slate-500 dark:text-zinc-400"><Zap :size="13" /> {{ days }} 天调用</p>
        <p class="mono mt-1 text-xl font-semibold">{{ usage?.window.calls ?? '—' }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="flex items-center gap-1.5 text-xs text-slate-500 dark:text-zinc-400"><Cpu :size="13" /> {{ days }} 天 tokens</p>
        <p class="mono mt-1 text-xl font-semibold">{{ usage?.window.total_tokens?.toLocaleString() ?? '—' }}</p>
      </div>
      <div class="panel px-4 py-3">
        <p class="flex items-center gap-1.5 text-xs text-slate-500 dark:text-zinc-400"><Coins :size="13" /> {{ days }} 天费用</p>
        <p class="mono mt-1 text-xl font-semibold">{{ usage?.window.cost?.toFixed(4) ?? '—' }}</p>
      </div>
    </div>

    <!-- 配置 -->
    <section class="panel p-5">
      <h2 class="text-sm font-semibold">接口配置</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        API Key <template v-if="configView?.api_key_set">已设置（留空保存 = 保留）</template><template v-else>未设置</template>；
        定价用于费用估算（每百万 token 单价，输入/输出分开），填 0 则费用记 0。
      </p>
      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <div class="sm:col-span-2">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">Base URL</label>
          <input v-model="config.base_url" placeholder="https://api.deepseek.com/v1" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">模型名</label>
          <input v-model="config.model" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">API Key（留空保留）</label>
          <input v-model="config.api_key" type="password" placeholder="sk-…" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div class="sm:col-span-2">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">
            密钥池（每行一个，留空保存 = 保留现有池）
            <template v-if="configView?.api_keys_count"> · 当前 {{ configView.api_keys_count }} 把（尾号 {{ configView.api_keys_tail.join(' / ') }}）</template>
          </label>
          <textarea v-model="config.api_keys" rows="3" placeholder="sk-…（每行一个，请求时自动轮询分摊，命中限流自动换下一把）" class="input mono mt-1 w-full px-3 py-2 text-sm"></textarea>
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">输入单价（/百万 tokens）</label>
          <input v-model.number="config.price_in" type="number" step="0.1" min="0" class="input mono mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">输出单价（/百万 tokens）</label>
          <input v-model.number="config.price_out" type="number" step="0.1" min="0" class="input mono mt-1 w-full px-3 py-2 text-sm">
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-2">
        <button class="btn-primary px-4 py-2" :disabled="busy" @click="saveConfig">{{ busyWhat === 'save' ? '保存中…' : '保存配置' }}</button>
        <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" :disabled="busy" @click="runTest">{{ busyWhat === 'test' ? '测试中…' : '连通测试' }}</button>
        <span v-if="testResult" class="text-xs" :class="testResult.includes('失败') || testResult.includes('未配置') ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'">{{ testResult }}</span>
      </div>
    </section>

    <!-- 批量任务 -->
    <section class="panel p-5">
      <h2 class="text-sm font-semibold">批量任务</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        平时事件摘要随采集自动触发；这里可后台补齐缺失与降级摘要，保留人工编辑和锁定内容。每批最多 200 条摘要或 50 条翻译，可稍后查看调用记录并继续补齐。翻译针对国际服公告与 YouTube 内容。
      </p>
      <div class="mt-3 flex flex-wrap gap-2">
        <button class="btn-primary px-4 py-2" :disabled="busy" @click="summarizeAll">{{ busyWhat === 'summarize' ? '执行中…' : '补事件摘要' }}</button>
        <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" :disabled="busy" @click="translateMissing">{{ busyWhat === 'translate' ? '执行中…' : '补内容翻译' }}</button>
      </div>
    </section>

    <!-- 用量 -->
    <section class="panel p-5">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold">用量统计</h2>
        <select v-model.number="days" class="select px-2 py-1 text-xs" @change="loadUsage">
          <option :value="1">近 1 天</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
      </div>
      <div class="mt-3 grid grid-cols-2 gap-3">
        <div class="rounded-xl bg-slate-500/5 px-4 py-3 dark:bg-white/5">
          <p class="text-xs text-slate-500 dark:text-zinc-400">累计 tokens</p>
          <p class="mono mt-1 text-lg font-semibold">{{ usage?.all_time.total_tokens?.toLocaleString() ?? '—' }}</p>
        </div>
        <div class="rounded-xl bg-slate-500/5 px-4 py-3 dark:bg-white/5">
          <p class="text-xs text-slate-500 dark:text-zinc-400">累计费用</p>
          <p class="mono mt-1 text-lg font-semibold">{{ usage?.all_time.cost?.toFixed(4) ?? '—' }}</p>
        </div>
      </div>
      <table v-if="usage?.by_task?.length" class="mt-4 w-full text-sm">
        <thead>
          <tr class="border-b border-slate-200 text-left text-xs text-slate-400 dark:border-zinc-800">
            <th class="py-2 pr-3 font-medium">任务</th>
            <th class="py-2 pr-3 font-medium">调用</th>
            <th class="py-2 pr-3 font-medium">输入</th>
            <th class="py-2 pr-3 font-medium">输出</th>
            <th class="py-2 pr-3 font-medium">tokens</th>
            <th class="py-2 pr-3 font-medium">费用</th>
            <th class="py-2 font-medium">平均耗时</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in usage.by_task" :key="t.task" class="border-b border-slate-100 last:border-0 dark:border-zinc-800/60">
            <td class="py-2 pr-3">{{ TASK_LABELS[t.task] ?? t.task }}</td>
            <td class="mono py-2 pr-3">{{ t.calls }}</td>
            <td class="mono py-2 pr-3">{{ t.prompt_tokens.toLocaleString() }}</td>
            <td class="mono py-2 pr-3">{{ t.completion_tokens.toLocaleString() }}</td>
            <td class="mono py-2 pr-3">{{ t.total_tokens.toLocaleString() }}</td>
            <td class="mono py-2 pr-3">{{ t.cost.toFixed(4) }}</td>
            <td class="mono py-2">{{ (t.avg_ms / 1000).toFixed(1) }}s</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="mt-3 text-sm text-slate-400">暂无 AI 调用记录。</p>
    </section>

    <!-- 最近调用 -->
    <section class="panel overflow-x-auto">
      <div class="px-5 py-3.5"><h2 class="text-sm font-semibold">最近调用</h2></div>
      <table v-if="calls.length" class="w-full min-w-2xl text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">时间</th>
            <th class="px-3 py-2.5 font-medium">任务</th>
            <th class="px-3 py-2.5 font-medium">tokens</th>
            <th class="px-3 py-2.5 font-medium">费用</th>
            <th class="px-3 py-2.5 font-medium">耗时</th>
            <th class="px-4 py-2.5 font-medium">状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in calls" :key="c.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="mono whitespace-nowrap px-4 py-2.5 text-xs">{{ new Date(c.created_at.endsWith('Z') ? c.created_at : c.created_at + 'Z').toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) }}</td>
            <td class="px-3 py-2.5">{{ TASK_LABELS[c.task] ?? c.task }}<span v-if="c.ref_id" class="mono ml-1 text-xs text-slate-400">#{{ c.ref_id }}</span></td>
            <td class="mono px-3 py-2.5">{{ c.total_tokens.toLocaleString() }}<span class="ml-1 text-xs text-slate-400">({{ c.prompt_tokens }}+{{ c.completion_tokens }})</span></td>
            <td class="mono px-3 py-2.5">{{ c.cost.toFixed(6) }}</td>
            <td class="mono px-3 py-2.5">{{ (c.duration_ms / 1000).toFixed(1) }}s</td>
            <td class="px-4 py-2.5">
              <span class="chip" :class="c.status === 'ok' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : ['fallback', 'partial'].includes(c.status) ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' : 'bg-red-500/10 text-red-600 dark:text-red-400'">{{ c.status === 'ok' ? '成功' : c.status === 'fallback' ? '校验回退' : c.status === 'partial' ? '部分通过' : '失败' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="px-5 py-8 text-center text-sm text-slate-400">暂无记录。</p>
    </section>
  </div>
</template>
