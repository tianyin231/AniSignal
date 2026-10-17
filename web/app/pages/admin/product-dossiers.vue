<script setup lang="ts">
/**
 * 管理端商品档案（批次 3b）：档案头维护、价格采样、AI 差评聚合、到价提醒台账。
 * 价格/热度都是「观测值」，管理端可手工补采样，但不能改 products 的履约字段。
 */
import { RefreshCw } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '商品档案 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()

interface DossierRow {
  product_id: number
  slug: string
  name: string
  community: string
  price_note: string | null
  has_profile: boolean
  cover_url: string | null
  official_url: string | null
  release_at: string | null
  release_state: string
  price_current: number | null
  price_observed_at: string | null
  heat_score: number
  price_points: number
  feedback_count: number
}

interface AlertRow {
  id: number
  user_id: number
  product_id: number
  product_name: string
  product_slug: string
  target_price: number
  enabled: boolean
  triggered_at: string | null
  triggered_price: number | null
  trigger_count: number
  created_at: string | null
}

interface FeedbackRow {
  id: number
  sentiment: string
  sentiment_label: string
  score: number
  summary: string
  source_method: string
  reviewed: boolean
  content_id: number | null
  url: string | null
  created_at: string | null
}

interface ProfilePayload {
  cover_url: string | null
  official_url: string | null
  official_name: string | null
  aliases: string[]
  release_at: string | null
  release_state: string
  release_note: string | null
  price_current: number | null
  price_currency: string
  price_source: string | null
  price_observed_at: string | null
  heat_score: number
  note: string | null
}

const releaseStates = ['unknown', 'announced', 'presale', 'on_sale', 'delayed', 'shipped']
const releaseStateLabels: Record<string, string> = {
  unknown: '未公布',
  announced: '已公布',
  presale: '预售中',
  on_sale: '已发售',
  delayed: '延期',
  shipped: '已发货',
}

const rows = ref<DossierRow[]>([])
const loading = ref(false)
const error = ref('')
const qInput = ref('')
const q = ref('')

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const data = await api<{ items: DossierRow[], total: number }>('/api/admin/product-dossiers', {
      query: q.value.trim() ? { q: q.value.trim() } : {},
    })
    rows.value = data.items
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

function applySearch() {
  q.value = qInput.value
  refresh()
}

// ---------- 档案头编辑 ----------

const editing = ref<DossierRow | null>(null)
const form = reactive({
  cover_url: '',
  official_url: '',
  official_name: '',
  aliases: '',
  release_at: '',
  release_state: 'unknown',
  release_note: '',
  price_current: '',
  note: '',
})
const saving = ref(false)

function toLocalInput(value: string | null) {
  if (!value)
    return ''
  return value.slice(0, 16)
}

function startEdit(row: DossierRow) {
  editing.value = row
  Object.assign(form, {
    cover_url: row.cover_url ?? '',
    official_url: row.official_url ?? '',
    official_name: '',
    aliases: '',
    release_at: toLocalInput(row.release_at),
    release_state: row.release_state,
    release_note: '',
    price_current: row.price_current === null ? '' : String(row.price_current),
    note: '',
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function resetForm() {
  editing.value = null
}

async function saveProfile() {
  if (!editing.value)
    return
  saving.value = true
  try {
    const payload: Record<string, unknown> = {
      release_state: form.release_state,
      release_at: form.release_at ? new Date(form.release_at).toISOString() : null,
      cover_url: form.cover_url || null,
      official_url: form.official_url || null,
      official_name: form.official_name || null,
      release_note: form.release_note || null,
      note: form.note || null,
      aliases: form.aliases.split(/[,，]/).map(item => item.trim()).filter(Boolean),
    }
    if (form.price_current)
      payload.price_current = Number(form.price_current)
    await api(`/api/admin/product-dossiers/${editing.value.product_id}`, { method: 'PUT', body: payload })
    toast.ok(`已保存「${editing.value.name}」的档案头`)
    resetForm()
    await refresh()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

// ---------- 采样 / 聚合 / 台账 ----------

const alerts = ref<AlertRow[]>([])
const alertsTotal = ref(0)
const alertsLoading = ref(false)

async function loadAlerts() {
  alertsLoading.value = true
  try {
    const data = await api<{ items: AlertRow[], total: number }>('/api/admin/product-dossiers/alerts', {
      query: { page: 1, page_size: 100 },
    })
    alerts.value = data.items
    alertsTotal.value = data.total
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '台账加载失败')
  } finally {
    alertsLoading.value = false
  }
}

onMounted(loadAlerts)

const busyAction = ref('')

async function runAction(key: string, path: string, okText: string, after?: () => Promise<void>) {
  busyAction.value = key
  try {
    const result = await api<Record<string, unknown>>(path, { method: 'POST' })
    toast.ok(`${okText}：${JSON.stringify(result)}`)
    if (after)
      await after()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '执行失败')
  } finally {
    busyAction.value = ''
  }
}

async function runPriceSweep() {
  await runAction('prices', '/api/admin/product-dossiers/prices/sweep', '价格采样完成', refresh)
}

async function runAlertSweep() {
  await runAction('alerts', '/api/admin/product-dossiers/alerts/sweep', '到价扫描完成', async () => {
    await loadAlerts()
    await refresh()
  })
}

async function runHeatRefresh() {
  await runAction('heat', '/api/admin/product-dossiers/heat/refresh', '热度重算完成', refresh)
}

// ---------- 质量反馈 ----------

const feedbackProduct = ref<DossierRow | null>(null)
const feedback = ref<FeedbackRow[]>([])
const feedbackBusy = ref(false)

async function openFeedback(row: DossierRow) {
  feedbackProduct.value = row
  feedbackBusy.value = true
  try {
    const data = await api<{ items: FeedbackRow[] }>(`/api/products/${row.slug}/feedback`, { query: { limit: 50 } })
    feedback.value = data.items
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '反馈加载失败')
  } finally {
    feedbackBusy.value = false
  }
}

async function aggregate(row: DossierRow) {
  feedbackBusy.value = true
  try {
    const result = await api<{ created: number, updated: number, ai: boolean }>(
      `/api/admin/product-dossiers/${row.product_id}/feedback/aggregate`,
      { method: 'POST' },
    )
    toast.ok(`聚合完成：新增 ${result.created}，更新 ${result.updated}，${result.ai ? 'AI' : '回退'}`)
    await openFeedback(row)
    await refresh()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '聚合失败')
  } finally {
    feedbackBusy.value = false
  }
}

async function review(item: FeedbackRow, reviewed: boolean) {
  try {
    await api(`/api/admin/product-dossiers/feedback/${item.id}`, { method: 'PATCH', body: { reviewed } })
    item.reviewed = reviewed
    toast.ok(reviewed ? '已确认该反馈' : '已撤销确认')
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '操作失败')
  }
}

// ---------- 手工补价 ----------

const priceProduct = ref<DossierRow | null>(null)
const priceForm = reactive({ price: '', heat: '', hits: '', note: '' })
const priceBusy = ref(false)

async function submitPrice() {
  if (!priceProduct.value || !priceForm.price)
    return
  priceBusy.value = true
  try {
    await api(`/api/admin/product-dossiers/${priceProduct.value.product_id}/prices`, {
      method: 'POST',
      body: {
        price: Number(priceForm.price),
        heat: priceForm.heat ? Number(priceForm.heat) : null,
        hits: priceForm.hits ? Number(priceForm.hits) : 0,
        note: priceForm.note || null,
      },
    })
    toast.ok('已记录采样点')
    priceProduct.value = null
    priceForm.price = ''
    priceForm.heat = ''
    priceForm.hits = ''
    priceForm.note = ''
    await refresh()
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '记录失败')
  } finally {
    priceBusy.value = false
  }
}

function fmt(value: string | null) {
  return value ? value.slice(0, 16).replace('T', ' ') : '—'
}
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center gap-2">
        <h1 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">商品档案</h1>
        <div class="ml-auto flex flex-wrap items-center gap-2">
          <input
            v-model="qInput"
            placeholder="搜索名称 / slug"
            class="input px-2.5 py-1.5 text-xs"
            @keyup.enter="applySearch"
          >
          <button class="btn-secondary px-3 py-1.5 text-xs" @click="applySearch">搜索</button>
          <button class="btn-secondary inline-flex items-center gap-1 px-3 py-1.5 text-xs" :disabled="loading" @click="refresh">
            <RefreshCw :size="13" /> 刷新
          </button>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <button class="btn-secondary px-3 py-1.5" :disabled="busyAction === 'prices'" @click="runPriceSweep">
          跑一次价格采样
        </button>
        <button class="btn-secondary px-3 py-1.5" :disabled="busyAction === 'alerts'" @click="runAlertSweep">
          跑一次到价扫描
        </button>
        <button class="btn-secondary px-3 py-1.5" :disabled="busyAction === 'heat'" @click="runHeatRefresh">
          重算热度分
        </button>
        <span class="text-slate-400">价格采样只认带货币符号/单位或「定价/售价/到手价」的数字。</span>
      </div>
      <p v-if="error" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ error }}</p>
    </section>

    <!-- 档案头编辑 -->
    <section v-if="editing" class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">编辑档案头：{{ editing.name }}</h2>
      <div class="mt-3 grid gap-2 sm:grid-cols-2">
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          封面 URL
          <input v-model="form.cover_url" class="input mt-1 w-full px-2.5 py-1.5 text-sm" placeholder="https://…">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          官方页面 URL
          <input v-model="form.official_url" class="input mt-1 w-full px-2.5 py-1.5 text-sm" placeholder="https://…">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          官方名称
          <input v-model="form.official_name" class="input mt-1 w-full px-2.5 py-1.5 text-sm">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          别名（逗号分隔）
          <input v-model="form.aliases" class="input mt-1 w-full px-2.5 py-1.5 text-sm" placeholder="鸣潮手办, 鸣潮 手办">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          发售时间
          <input v-model="form.release_at" type="datetime-local" class="input mt-1 w-full px-2.5 py-1.5 text-sm">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          发售状态
          <select v-model="form.release_state" class="select mt-1 w-full px-2.5 py-1.5 text-sm">
            <option v-for="state in releaseStates" :key="state" :value="state">{{ releaseStateLabels[state] }}</option>
          </select>
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          当前观测价
          <input v-model="form.price_current" type="number" min="0" step="0.01" class="input mt-1 w-full px-2.5 py-1.5 text-sm">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          发售备注
          <input v-model="form.release_note" class="input mt-1 w-full px-2.5 py-1.5 text-sm">
        </label>
      </div>
      <div class="mt-3 flex items-center gap-2">
        <button class="btn-primary px-4 py-2 text-sm" :disabled="saving" @click="saveProfile">保存</button>
        <button class="btn-secondary px-4 py-2 text-sm" @click="resetForm">取消</button>
        <span class="text-xs text-slate-400">只更新填了的字段，未填的保持原值。</span>
      </div>
    </section>

    <!-- 商品列表 -->
    <section class="panel overflow-hidden">
      <table class="w-full text-left text-xs">
        <thead class="bg-slate-500/5 text-slate-500 dark:text-zinc-400">
          <tr>
            <th class="px-3 py-2 font-medium">商品</th>
            <th class="px-3 py-2 font-medium">发售</th>
            <th class="px-3 py-2 font-medium">观测价</th>
            <th class="px-3 py-2 font-medium">热度</th>
            <th class="px-3 py-2 font-medium">曲线</th>
            <th class="px-3 py-2 font-medium">反馈</th>
            <th class="px-3 py-2 font-medium">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-900/5 dark:divide-white/10">
          <tr v-for="row in rows" :key="row.product_id">
            <td class="px-3 py-2">
              <NuxtLink :to="`/products/${row.slug}?view=dossier`" class="font-medium hover:underline">{{ row.name }}</NuxtLink>
              <span class="mono block text-[11px] text-slate-400">{{ row.slug }}</span>
            </td>
            <td class="px-3 py-2">
              <span class="chip whitespace-nowrap bg-blue-500/10 text-blue-700 dark:text-blue-300">
                {{ releaseStateLabels[row.release_state] ?? row.release_state }}
              </span>
              <span class="mono block text-[11px] text-slate-400">{{ fmt(row.release_at) }}</span>
            </td>
            <td class="mono px-3 py-2">{{ row.price_current === null ? '—' : `¥${row.price_current}` }}</td>
            <td class="mono px-3 py-2">{{ row.heat_score }}</td>
            <td class="mono px-3 py-2">{{ row.price_points }}</td>
            <td class="mono px-3 py-2">{{ row.feedback_count }}</td>
            <td class="px-3 py-2">
              <div class="flex flex-wrap gap-2">
                <button class="link" @click="startEdit(row)">档案头</button>
                <button
                  class="link"
                  @click="() => { priceProduct = row; priceForm.price = row.price_current === null ? '' : String(row.price_current) }"
                >补价</button>
                <button class="link" :disabled="feedbackBusy" @click="aggregate(row)">聚合反馈</button>
                <button class="link" @click="openFeedback(row)">看反馈</button>
              </div>
            </td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="7" class="px-3 py-8 text-center text-slate-400">没有匹配的商品。</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 补价弹层 -->
    <section v-if="priceProduct" class="panel p-4">
      <h2 class="text-sm font-semibold">手工补采样：{{ priceProduct.name }}</h2>
      <div class="mt-2 flex flex-wrap items-end gap-2 text-xs">
        <label class="text-slate-500 dark:text-zinc-400">
          价格
          <input v-model="priceForm.price" type="number" min="0" step="0.01" class="input mt-1 w-28 px-2 py-1.5 text-sm">
        </label>
        <label class="text-slate-500 dark:text-zinc-400">
          热度
          <input v-model="priceForm.heat" type="number" min="0" step="0.1" class="input mt-1 w-24 px-2 py-1.5 text-sm">
        </label>
        <label class="text-slate-500 dark:text-zinc-400">
          命中数
          <input v-model="priceForm.hits" type="number" min="0" class="input mt-1 w-20 px-2 py-1.5 text-sm">
        </label>
        <label class="text-slate-500 dark:text-zinc-400">
          备注
          <input v-model="priceForm.note" class="input mt-1 w-48 px-2 py-1.5 text-sm" placeholder="外部渠道">
        </label>
        <button class="btn-primary px-3 py-1.5" :disabled="priceBusy" @click="submitPrice">记录</button>
        <button class="btn-secondary px-3 py-1.5" @click="priceProduct = null">取消</button>
      </div>
    </section>

    <!-- 质量反馈 -->
    <section v-if="feedbackProduct" class="panel p-4 sm:p-5">
      <div class="flex items-center gap-2">
        <h2 class="text-sm font-semibold">质量反馈：{{ feedbackProduct.name }}</h2>
        <button class="link ml-auto text-xs" @click="feedbackProduct = null">收起</button>
      </div>
      <ul class="mt-3 space-y-2">
        <li
          v-for="item in feedback"
          :key="item.id"
          class="rounded-md border border-slate-900/5 p-3 text-xs dark:border-white/10"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="chip bg-rose-500/10 text-rose-700 dark:text-rose-300">{{ item.sentiment_label }}</span>
            <span class="mono text-slate-400">{{ item.source_method === 'ai' ? 'AI 摘要' : '原文片段' }}</span>
            <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link ml-auto">原文</a>
            <button class="link" @click="review(item, !item.reviewed)">
              {{ item.reviewed ? '撤销确认' : '确认' }}
            </button>
          </div>
          <p class="mt-1 leading-relaxed text-slate-700 dark:text-zinc-200">{{ item.summary }}</p>
        </li>
        <li v-if="!feedback.length && !feedbackBusy" class="text-slate-400">还没有反馈记录，先点「聚合反馈」。</li>
      </ul>
    </section>

    <!-- 到价提醒台账 -->
    <section class="panel overflow-hidden">
      <div class="flex items-center gap-2 border-b border-slate-900/5 px-4 py-3 dark:border-white/10">
        <h2 class="text-sm font-semibold">到价提醒台账（{{ alertsTotal }}）</h2>
        <button class="link ml-auto text-xs" :disabled="alertsLoading" @click="loadAlerts">刷新</button>
      </div>
      <table class="w-full text-left text-xs">
        <thead class="bg-slate-500/5 text-slate-500 dark:text-zinc-400">
          <tr>
            <th class="px-3 py-2 font-medium">商品</th>
            <th class="px-3 py-2 font-medium">用户</th>
            <th class="px-3 py-2 font-medium">心理价位</th>
            <th class="px-3 py-2 font-medium">状态</th>
            <th class="px-3 py-2 font-medium">触发次数</th>
            <th class="px-3 py-2 font-medium">最近触发</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-900/5 dark:divide-white/10">
          <tr v-for="alert in alerts" :key="alert.id">
            <td class="px-3 py-2">
              <NuxtLink :to="`/products/${alert.product_slug}?view=dossier`" class="hover:underline">{{ alert.product_name }}</NuxtLink>
            </td>
            <td class="mono px-3 py-2">#{{ alert.user_id }}</td>
            <td class="mono px-3 py-2">¥{{ alert.target_price }}</td>
            <td class="px-3 py-2">
              <span class="chip" :class="alert.enabled
                ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
              >
                {{ alert.enabled ? '启用' : '静音' }}
              </span>
            </td>
            <td class="mono px-3 py-2">{{ alert.trigger_count }}</td>
            <td class="mono px-3 py-2">
              {{ fmt(alert.triggered_at) }}
              <span v-if="alert.triggered_price !== null" class="block text-[11px] text-slate-400">¥{{ alert.triggered_price }}</span>
            </td>
          </tr>
          <tr v-if="!alerts.length && !alertsLoading">
            <td colspan="6" class="px-3 py-8 text-center text-slate-400">还没有用户设过心理价位。</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
