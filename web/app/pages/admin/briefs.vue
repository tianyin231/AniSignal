<script setup lang="ts">
/**
 * 管理端每日情报简报（批次 3c）：历史列表/详情/试跑/补生成/重推。
 * 预览与真实邮件共用同一份排版（render_brief_text）。
 */
import { RefreshCw } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '每日简报 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()

interface BriefItem {
  id: number
  brief_date: string
  title: string
  summary: string | null
  sections: Record<string, any>
  stats: Record<string, number>
  status: string
  ai_status: string
  model: string | null
  error: string | null
  delivered: Record<string, unknown>
  sent_at: string | null
  created_at: string | null
}

const items = ref<BriefItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const selected = ref<BriefItem | null>(null)
const preview = ref<{ brief_date: string, title: string, summary: string, ai_status: string, stored: number, sections: Record<string, any> } | null>(null)
const generateDay = ref('')
const busy = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function refresh() {
  loading.value = true
  try {
    const data = await api<{ items: BriefItem[], total: number }>('/api/admin/briefs', {
      query: { page: page.value, page_size: pageSize },
    })
    items.value = data.items
    total.value = data.total
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
watch(page, refresh)

async function openDetail(row: BriefItem) {
  try {
    selected.value = await api<BriefItem>(`/api/admin/briefs/${row.id}`)
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '详情加载失败')
  }
}

async function runPreview() {
  busy.value = 'preview'
  try {
    const query = generateDay.value ? { day: generateDay.value } : {}
    preview.value = await api<typeof preview.value>('/api/admin/briefs/preview', { query })
    toast.ok('试跑完成（未落库、未推送）')
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '试跑失败')
  } finally {
    busy.value = ''
  }
}

async function generate() {
  busy.value = 'generate'
  try {
    const result = await api<{ id: number, created: boolean, brief_date: string }>(
      '/api/admin/briefs/generate',
      { method: 'POST', body: generateDay.value ? { day: generateDay.value } : {} },
    )
    toast.ok(result.created ? `已生成 ${result.brief_date} 的简报` : `${result.brief_date} 的简报已存在，已重算`)
    page.value = 1
    await refresh()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '生成失败')
  } finally {
    busy.value = ''
  }
}

async function send(row: BriefItem) {
  busy.value = `send-${row.id}`
  try {
    const result = await api<{ email: number, qq: number, status: string }>(`/api/admin/briefs/${row.id}/send`, { method: 'POST' })
    if (result.status === 'sent')
      toast.ok(`已推送：邮件 ${result.email}，QQ ${result.qq}`)
    else
      toast.err('没有可用渠道：未配置收件人或未启用机器人目标')
    await refresh()
    if (selected.value?.id === row.id)
      await openDetail(row)
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '推送失败')
  } finally {
    busy.value = ''
  }
}

function statusTone(status: string) {
  if (status === 'sent')
    return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  if (status === 'failed')
    return 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
  return 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'
}

const statusLabels: Record<string, string> = { ready: '待推送', sent: '已推送', failed: '推送失败' }
const aiLabels: Record<string, string> = { ai: 'AI 撰写', fallback: '模板文' }
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center gap-2">
        <h1 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">每日情报简报</h1>
        <button class="btn-secondary ml-auto inline-flex items-center gap-1 px-3 py-1.5 text-xs" :disabled="loading" @click="refresh">
          <RefreshCw :size="13" /> 刷新
        </button>
      </div>
      <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">
        每天北京时间 08:00 后自动生成前一天的简报并推送；同一天重复跑不会重复生成或重复发送。
      </p>
      <div class="mt-3 flex flex-wrap items-end gap-2 text-xs">
        <label class="text-slate-500 dark:text-zinc-400">
          日期（留空为昨天）
          <input v-model="generateDay" type="date" class="input mt-1 px-2.5 py-1.5 text-sm">
        </label>
        <button class="btn-secondary px-3 py-1.5" :disabled="busy === 'preview'" @click="runPreview">试跑预览</button>
        <button class="btn-primary px-3 py-1.5" :disabled="busy === 'generate'" @click="generate">生成简报</button>
      </div>
    </section>

    <!-- 试跑预览 -->
    <section v-if="preview" class="panel p-4 sm:p-5">
      <div class="flex items-center gap-2">
        <h2 class="text-sm font-semibold">试跑：{{ preview.title }}</h2>
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">
          {{ aiLabels[preview.ai_status] ?? preview.ai_status }}
        </span>
        <button class="link ml-auto text-xs" @click="preview = null">收起</button>
      </div>
      <p class="mt-2 text-[13px] leading-relaxed text-slate-700 dark:text-zinc-200">{{ preview.summary }}</p>
      <pre class="mono mt-3 max-h-64 overflow-auto rounded-md bg-slate-500/5 p-3 text-[11px] leading-relaxed text-slate-600 dark:text-zinc-300">{{ JSON.stringify(preview.sections, null, 2) }}</pre>
      <p class="mt-2 text-xs text-slate-400">该日期已有 {{ preview.stored }} 份存档；试跑不会写入数据库，也不会推送。</p>
    </section>

    <!-- 历史列表 -->
    <section class="panel overflow-hidden">
      <table class="w-full text-left text-xs">
        <thead class="bg-slate-500/5 text-slate-500 dark:text-zinc-400">
          <tr>
            <th class="px-3 py-2 font-medium">日期</th>
            <th class="px-3 py-2 font-medium">命中</th>
            <th class="px-3 py-2 font-medium">事件 / 商品</th>
            <th class="px-3 py-2 font-medium">撰写</th>
            <th class="px-3 py-2 font-medium">状态</th>
            <th class="px-3 py-2 font-medium">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-900/5 dark:divide-white/10">
          <tr v-for="row in items" :key="row.id">
            <td class="mono px-3 py-2">{{ row.brief_date }}</td>
            <td class="mono px-3 py-2">{{ row.stats.volume_total ?? 0 }}</td>
            <td class="mono px-3 py-2">{{ row.stats.events ?? 0 }} / {{ row.stats.products ?? 0 }}</td>
            <td class="px-3 py-2">
              <span class="chip whitespace-nowrap" :class="row.ai_status === 'ai'
                ? 'bg-blue-500/10 text-blue-700 dark:text-blue-300'
                : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
              >
                {{ aiLabels[row.ai_status] ?? row.ai_status }}
              </span>
            </td>
            <td class="px-3 py-2">
              <span class="chip whitespace-nowrap" :class="statusTone(row.status)">
                {{ statusLabels[row.status] ?? row.status }}
              </span>
            </td>
            <td class="px-3 py-2">
              <div class="flex flex-wrap gap-2">
                <button class="link" @click="openDetail(row)">详情</button>
                <button class="link" :disabled="busy === `send-${row.id}`" @click="send(row)">重推</button>
              </div>
            </td>
          </tr>
          <tr v-if="!items.length && !loading">
            <td colspan="6" class="px-3 py-8 text-center text-slate-400">还没有生成过简报。</td>
          </tr>
        </tbody>
      </table>
      <div v-if="totalPages > 1" class="flex items-center justify-end gap-2 border-t border-slate-900/5 px-3 py-2 text-xs dark:border-white/10">
        <button class="btn-secondary px-2.5 py-1" :disabled="page <= 1" @click="page -= 1">上一页</button>
        <span class="mono text-slate-400">{{ page }} / {{ totalPages }}</span>
        <button class="btn-secondary px-2.5 py-1" :disabled="page >= totalPages" @click="page += 1">下一页</button>
      </div>
    </section>

    <!-- 详情 -->
    <section v-if="selected" class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="text-sm font-semibold">{{ selected.title }}</h2>
        <span class="chip" :class="statusTone(selected.status)">{{ statusLabels[selected.status] ?? selected.status }}</span>
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">
          {{ aiLabels[selected.ai_status] ?? selected.ai_status }}
        </span>
        <button class="link ml-auto text-xs" @click="selected = null">收起</button>
      </div>
      <p v-if="selected.error" class="mt-2 rounded-md bg-rose-500/5 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
        {{ selected.error }}
      </p>
      <p class="mt-2 text-[13px] leading-relaxed text-slate-700 dark:text-zinc-200">{{ selected.summary }}</p>
      <dl class="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div>
          <dt class="text-slate-400">命中</dt>
          <dd class="mono">{{ selected.stats.volume_total ?? 0 }}</dd>
        </div>
        <div>
          <dt class="text-slate-400">事件</dt>
          <dd class="mono">{{ selected.stats.events ?? 0 }}</dd>
        </div>
        <div>
          <dt class="text-slate-400">重要命中</dt>
          <dd class="mono">{{ selected.stats.top_hits ?? 0 }}</dd>
        </div>
        <div>
          <dt class="text-slate-400">商品</dt>
          <dd class="mono">{{ selected.stats.products ?? 0 }}</dd>
        </div>
      </dl>

      <div class="mt-4 space-y-3">
        <div v-if="selected.sections.events?.length">
          <h3 class="text-xs font-semibold text-slate-600 dark:text-zinc-300">昨日事件汇总</h3>
          <ul class="mt-1 space-y-1 text-xs text-slate-600 dark:text-zinc-300">
            <li v-for="event in selected.sections.events" :key="event.event_id">
              <span v-if="event.importance" class="text-amber-600 dark:text-amber-400">★</span>
              {{ event.title }}（证据 {{ event.evidence_count }} 条）
            </li>
          </ul>
        </div>
        <div v-if="selected.sections.top_hits?.length">
          <h3 class="text-xs font-semibold text-slate-600 dark:text-zinc-300">重要命中 Top</h3>
          <ol class="mt-1 list-decimal space-y-1 pl-4 text-xs text-slate-600 dark:text-zinc-300">
            <li v-for="hit in selected.sections.top_hits" :key="hit.content_id">
              <a v-if="hit.url" :href="hit.url" target="_blank" rel="noopener noreferrer" class="hover:underline">{{ hit.title }}</a>
              <span v-else>{{ hit.title }}</span>
              <span class="text-slate-400">（{{ hit.platform }}）</span>
            </li>
          </ol>
        </div>
        <div v-if="selected.sections.products?.length">
          <h3 class="text-xs font-semibold text-slate-600 dark:text-zinc-300">商品进展</h3>
          <ul class="mt-1 space-y-1 text-xs text-slate-600 dark:text-zinc-300">
            <li v-for="product in selected.sections.products" :key="product.product_id">
              <NuxtLink :to="`/products/${product.slug}?view=dossier`" class="hover:underline">{{ product.name }}</NuxtLink>
              ：昨日新增证据 {{ product.new_evidence }} 条
            </li>
          </ul>
        </div>
        <div v-if="selected.sections.volume">
          <h3 class="text-xs font-semibold text-slate-600 dark:text-zinc-300">声量回顾</h3>
          <p class="mt-1 text-xs text-slate-600 dark:text-zinc-300">
            命中 {{ selected.sections.volume.total }} 条
            <span v-for="(count, platform) in selected.sections.volume.by_platform" :key="platform" class="ml-2 text-slate-400">
              {{ platform }} {{ count }}
            </span>
          </p>
        </div>
      </div>

      <details class="mt-4">
        <summary class="cursor-pointer text-xs text-slate-500 dark:text-zinc-400">查看结构化数据</summary>
        <pre class="mono mt-2 max-h-72 overflow-auto rounded-md bg-slate-500/5 p-3 text-[11px] leading-relaxed text-slate-600 dark:text-zinc-300">{{ JSON.stringify(selected.sections, null, 2) }}</pre>
      </details>
    </section>
  </div>
</template>
