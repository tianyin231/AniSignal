<script setup lang="ts">
import type { ApiProduct, ApiProductList, ApiProductSignal, ApiProductSignalList } from '~/composables/usePublicApi'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '商品管理 · AniSignal 管理看板' })

const { api } = useAdminApi()
const products = ref<ApiProduct[]>([])
const error = ref<string | null>(null)
const busy = ref(false)
const toast = useAdminToast()

const form = reactive({
  slug: '',
  name: '',
  publisher: '',
  skus: '',
  price_note: '',
  presale_at: '',
  balance_window: '',
  original_ship_at: '',
  ship_note: '',
  watch_terms: '',
  create_bilibili_target: true,
  monitor_keyword: '',
})
const editing = ref<ApiProduct | null>(null)
const termsTouched = ref(false)

const signalProduct = ref<ApiProduct | null>(null)
const signals = ref<ApiProductSignal[]>([])
const signalsBusy = ref(false)
const editingSignal = ref<ApiProductSignal | null>(null)
const signalForm = reactive({
  review_status: 'candidate',
  actor_type: 'unknown',
  acquisition_type: 'unknown',
  summary: '',
  claim_quote: '',
  note: '',
})

const signalStatusLabel = {
  candidate: '待核验',
  verified: '已核验',
  rejected: '已驳回',
  conflict: '证据冲突',
} as const

function suggestedTerms(name: string) {
  const full = name.replace(/\s+/g, '').trim()
  const compact = full.replace(/联名|机械|限定|周边/g, '')
  return [...new Set([full, compact])].filter((term) => term.length >= 2)
}

watch(
  () => form.name,
  (name) => {
    if (!editing.value && !termsTouched.value)
      form.watch_terms = suggestedTerms(name).join(', ')
  },
)

async function refresh() {
  try {
    const data = await api<ApiProductList>('/api/admin/products')
    products.value = data.items
    if (signalProduct.value)
      signalProduct.value = data.items.find((item) => item.id === signalProduct.value?.id) ?? null
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}
onMounted(refresh)

function resetForm() {
  editing.value = null
  termsTouched.value = false
  Object.assign(form, {
    slug: '', name: '', publisher: '', skus: '', price_note: '', presale_at: '',
    balance_window: '', original_ship_at: '', ship_note: '', watch_terms: '',
    create_bilibili_target: true, monitor_keyword: '',
  })
}

function startEdit(product: ApiProduct) {
  editing.value = product
  termsTouched.value = true
  Object.assign(form, {
    slug: product.slug,
    name: product.name,
    publisher: product.publisher ?? '',
    skus: product.skus.join(', '),
    price_note: product.price_note ?? '',
    presale_at: product.presale_at ?? '',
    balance_window: product.balance_window ?? '',
    original_ship_at: product.original_ship_at ?? '',
    ship_note: product.ship_note ?? '',
    watch_terms: product.watch_terms.join(', '),
    create_bilibili_target: false,
    monitor_keyword: '',
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function listValue(value: string) {
  return [...new Set(value.split(/[,，]/).map((item) => item.trim()).filter(Boolean))]
}

async function submit() {
  if (!form.name.trim()) return
  busy.value = true
  error.value = null
  const body = {
    name: form.name.trim(),
    publisher: form.publisher.trim() || null,
    skus: listValue(form.skus),
    price_note: form.price_note.trim() || null,
    presale_at: form.presale_at.trim() || null,
    balance_window: form.balance_window.trim() || null,
    original_ship_at: form.original_ship_at.trim() || null,
    ship_note: form.ship_note.trim() || null,
    watch_terms: listValue(form.watch_terms),
  }
  try {
    if (editing.value) {
      await api(`/api/admin/products/${editing.value.id}`, { method: 'PUT', body })
      toast.ok('商品已更新，并重新匹配历史内容')
    } else {
      await api('/api/admin/products', {
        method: 'POST',
        body: {
          ...body,
          slug: form.slug.trim() || null,
          create_bilibili_target: form.create_bilibili_target,
          monitor_keyword: form.monitor_keyword.trim() || null,
        },
      })
      toast.ok(form.create_bilibili_target ? '商品与 B 站监控已创建' : '商品已创建')
    }
    resetForm()
    await refresh()
  } catch (exc: any) {
    error.value = exc?.statusCode === 409 ? 'URL 标识已存在' : `保存失败：${exc?.data?.detail ?? exc?.message}`
    toast.err(error.value)
  } finally {
    busy.value = false
  }
}

async function remove(product: ApiProduct) {
  if (!confirm(`删除商品「${product.name}」？关联事件会保留但失去商品关联。`)) return
  busy.value = true
  try {
    await api(`/api/admin/products/${product.id}`, { method: 'DELETE' })
    toast.ok(`商品「${product.name}」已删除`)
    await refresh()
  } finally {
    busy.value = false
  }
}

async function openSignals(product: ApiProduct) {
  signalProduct.value = product
  editingSignal.value = null
  signalsBusy.value = true
  try {
    const data = await api<ApiProductSignalList>(`/api/admin/products/${product.id}/signals`)
    signals.value = [...data.items].sort((left, right) =>
      Number(right.review_status === 'candidate') - Number(left.review_status === 'candidate'),
    )
  } catch (exc: any) {
    error.value = `读取信号失败：${exc?.data?.detail ?? exc?.message}`
    toast.err(error.value)
  } finally {
    signalsBusy.value = false
  }
}

function startSignalReview(signal: ApiProductSignal) {
  editingSignal.value = signal
  Object.assign(signalForm, {
    review_status: signal.review_status,
    actor_type: signal.actor_type,
    acquisition_type: signal.acquisition_type,
    summary: signal.summary,
    claim_quote: signal.claim_quote ?? '',
    note: signal.review_note ?? '',
  })
}

async function saveSignal() {
  if (!signalProduct.value || !editingSignal.value) return
  signalsBusy.value = true
  try {
    await api(`/api/admin/products/${signalProduct.value.id}/signals/${editingSignal.value.id}`, {
      method: 'PATCH',
      body: {
        ...signalForm,
        summary: signalForm.summary.trim(),
        claim_quote: signalForm.claim_quote.trim() || null,
        note: signalForm.note.trim(),
      },
    })
    toast.ok('履约信号审核已保存')
    editingSignal.value = null
    await refresh()
    if (signalProduct.value)
      await openSignals(signalProduct.value)
  } catch (exc: any) {
    error.value = `审核失败：${exc?.data?.detail ?? exc?.message}`
    toast.err(error.value)
  } finally {
    signalsBusy.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-lg font-semibold">商品管理</h1>
      <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
        新增时只填商品名称即可；系统自动生成精确匹配词并创建采集。规则或 AI 只生成候选信号，公开结论必须逐条核验。
      </p>
    </div>

    <form class="panel p-5" @submit.prevent="submit">
      <div class="flex items-center justify-between gap-3">
        <h2 class="text-sm font-semibold">{{ editing ? `编辑：${editing.name}` : '快速新增商品' }}</h2>
        <button v-if="editing" type="button" class="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-zinc-300" @click="resetForm">取消编辑</button>
      </div>
      <div class="mt-4 max-w-xl">
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          商品名称 <span class="text-red-500">*</span>
          <input v-model="form.name" required class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="如：爱弥斯联名机械键盘">
        </label>
      </div>

      <label v-if="!editing" class="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 text-sm">
        <input v-model="form.create_bilibili_target" type="checkbox" class="mt-0.5 accent-blue-600">
        <span>
          <span class="font-medium text-slate-700 dark:text-zinc-200">同时创建 B 站关键词监控</span>
          <span class="mt-0.5 block text-xs text-slate-500 dark:text-zinc-400">默认按完整商品名、每 30 分钟采集；已有同名监控会直接复用。</span>
        </span>
      </label>

      <details class="mt-4 rounded-lg border border-slate-900/10 p-3 dark:border-white/10" :open="!!editing">
        <summary class="cursor-pointer text-xs font-medium text-slate-500 dark:text-zinc-400">高级匹配与商品信息</summary>
        <div class="mt-3 grid gap-3 sm:grid-cols-3">
          <label class="text-xs text-slate-500 sm:col-span-2 dark:text-zinc-400">
            内容匹配词
            <input v-model="form.watch_terms" required class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="逗号分隔" @input="termsTouched = true">
            <span class="mt-1 block text-[11px] text-slate-400">已按商品名自动生成；避免改成角色名等过宽词。</span>
          </label>
          <input v-model="form.slug" :disabled="!!editing" placeholder="URL 标识，留空自动生成" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm disabled:opacity-50 dark:border-white/15 dark:bg-white/5">
          <input v-model="form.publisher" placeholder="发行方" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.skus" placeholder="SKU（逗号分隔）" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.price_note" placeholder="价格说明" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.presale_at" placeholder="预售日期" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.balance_window" placeholder="尾款窗口" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.original_ship_at" placeholder="原定发货" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.ship_note" placeholder="发货说明" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-if="!editing && form.create_bilibili_target" v-model="form.monitor_keyword" placeholder="B站搜索词，留空用完整商品名" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
      </details>

      <div class="mt-4 flex items-center gap-3">
        <button type="submit" class="btn-primary px-4 py-2" :disabled="busy || !form.name.trim() || !form.watch_terms.trim()">
          {{ busy ? '处理中…' : editing ? '保存修改' : '创建并回看历史内容' }}
        </button>
        <p v-if="error" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
      </div>
    </form>

    <section v-if="signalProduct" class="panel p-5">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold">审核履约信号：{{ signalProduct.name }}</h2>
          <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">逐条确认主体、获取方式与原文摘录；只有“已核验”信号会影响公开三轴状态。</p>
        </div>
        <button class="min-h-11 px-2 text-xs text-slate-400 hover:text-slate-600" @click="signalProduct = null; editingSignal = null">关闭</button>
      </div>
      <p v-if="signalsBusy && !signals.length" class="py-8 text-center text-sm text-slate-400">读取中…</p>
      <div v-else-if="signals.length" class="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div class="max-h-[34rem] space-y-2 overflow-y-auto pr-1">
          <button
            v-for="signal in signals"
            :key="signal.id"
            type="button"
            class="min-h-11 w-full rounded-lg border p-3 text-left transition-colors hover:bg-slate-500/5 dark:border-white/10"
            :class="editingSignal?.id === signal.id ? 'border-blue-500/40 bg-blue-500/5' : 'border-slate-900/10'"
            @click="startSignalReview(signal)"
          >
            <span class="flex flex-wrap items-center gap-2">
              <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ signal.signal_label }}</span>
              <span class="chip" :class="signal.review_status === 'verified' ? 'bg-emerald-500/10 text-emerald-700' : signal.review_status === 'candidate' ? 'bg-amber-500/10 text-amber-700' : 'bg-slate-500/10 text-slate-500'">{{ signalStatusLabel[signal.review_status] }}</span>
            </span>
            <span class="mt-2 block text-sm text-slate-700 dark:text-zinc-200">{{ signal.summary }}</span>
            <span class="mt-1 block text-xs text-slate-400">{{ signal.account || signal.platform }} · 原文 #{{ signal.content_id }}</span>
          </button>
        </div>

        <form v-if="editingSignal" class="rounded-lg border border-slate-900/10 p-4 dark:border-white/10" @submit.prevent="saveSignal">
          <p class="text-sm font-medium">审核 #{{ editingSignal.id }}</p>
          <div class="mt-3 grid gap-3">
            <label class="text-xs text-slate-500">审核状态
              <select v-model="signalForm.review_status" class="mt-1 min-h-11 w-full rounded-md border border-slate-900/10 bg-white px-3 text-sm dark:border-white/15 dark:bg-zinc-900">
                <option value="candidate">待核验</option><option value="verified">已核验</option><option value="rejected">驳回</option><option value="conflict">证据冲突</option>
              </select>
            </label>
            <div class="grid grid-cols-2 gap-3">
              <label class="text-xs text-slate-500">主体
                <select v-model="signalForm.actor_type" class="mt-1 min-h-11 w-full rounded-md border border-slate-900/10 bg-white px-2 text-sm dark:border-white/15 dark:bg-zinc-900">
                  <option value="unknown">待确认</option><option value="official">官方</option><option value="user">普通用户</option><option value="seeded">送测账号</option><option value="media">媒体</option><option value="merchant">商家</option>
                </select>
              </label>
              <label class="text-xs text-slate-500">获取方式
                <select v-model="signalForm.acquisition_type" class="mt-1 min-h-11 w-full rounded-md border border-slate-900/10 bg-white px-2 text-sm dark:border-white/15 dark:bg-zinc-900">
                  <option value="unknown">待确认</option><option value="self_purchase">自购</option><option value="seeded">送测</option>
                </select>
              </label>
            </div>
            <label class="text-xs text-slate-500">事实摘要
              <textarea v-model="signalForm.summary" rows="2" required class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"></textarea>
            </label>
            <label class="text-xs text-slate-500">原文摘录
              <textarea v-model="signalForm.claim_quote" rows="3" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"></textarea>
            </label>
            <label class="text-xs text-slate-500">审核备注
              <textarea v-model="signalForm.note" rows="2" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"></textarea>
            </label>
          </div>
          <div class="mt-4 flex items-center gap-3">
            <button type="submit" class="btn-primary min-h-11 px-4" :disabled="signalsBusy || !signalForm.summary.trim() || (signalForm.review_status === 'verified' && !signalForm.claim_quote.trim())">{{ signalsBusy ? '保存中…' : '保存审核' }}</button>
            <NuxtLink :to="`/contents/${editingSignal.content_id}`" target="_blank" class="link min-h-11 inline-flex items-center text-xs">查看原文</NuxtLink>
          </div>
        </form>
      </div>
      <p v-else class="mt-4 rounded-lg border border-dashed border-slate-900/15 p-6 text-center text-sm text-slate-400 dark:border-white/15">暂无履约信号。先采集并聚合该商品的官方、到货或质量内容。</p>
    </section>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-[900px] text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">商品</th>
            <th class="px-3 py-2.5 font-medium">监控词</th>
            <th class="px-3 py-2.5 font-medium">官方履约</th>
            <th class="px-3 py-2.5 font-medium">消费者样本</th>
            <th class="px-3 py-2.5 font-medium">质量风险</th>
            <th class="px-4 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in products" :key="p.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="px-4 py-3">
              <span class="font-medium">{{ p.name }}</span>
              <span class="mono ml-2 text-xs text-slate-400">{{ p.slug }}</span>
            </td>
            <td class="max-w-48 truncate px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.watch_terms.join(' / ') || '—' }}</td>
            <td class="max-w-48 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.fulfillment.official.state_label }}</td>
            <td class="max-w-52 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.fulfillment.consumer.state_label }}</td>
            <td class="max-w-48 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.fulfillment.quality.state_label }}</td>
            <td class="whitespace-nowrap px-4 py-3 text-right">
              <button class="min-h-11 rounded px-2 text-xs text-emerald-700 hover:underline dark:text-emerald-300" @click="openSignals(p)">审核信号<span v-if="p.fulfillment.candidate_total">（{{ p.fulfillment.candidate_total }}）</span></button>
              <button class="min-h-11 rounded px-2 text-xs text-blue-600 hover:underline dark:text-blue-300" @click="startEdit(p)">编辑</button>
              <button class="min-h-11 rounded px-2 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busy" @click="remove(p)">删除</button>
            </td>
          </tr>
          <tr v-if="!products.length">
            <td colspan="6" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">暂无商品</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
