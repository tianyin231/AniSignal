<script setup lang="ts">
import type { ApiProduct, ApiProductList, ApiProductSignal, ApiProductSignalList } from '~/composables/usePublicApi'
import { ADMIN_COMMUNITIES, ADMIN_COMMUNITY_BADGE, ADMIN_COMMUNITY_LABELS } from '~/utils/admin-communities'
import { productSignalReviewLabel } from '~/utils/product-utils'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '商品管理 · AniSignal 管理看板' })

const { api } = useAdminApi()

const fulfillmentKindLabels: Record<string, string> = {
  self_purchase: '自购开箱',
  pr_sample: '送测样品',
  forward: '转发剪辑',
  none: '普通资讯',
}
const products = ref<ApiProduct[]>([])
const communityFilter = ref<string>('')
const filteredProducts = computed(() =>
  communityFilter.value ? products.value.filter(p => p.community === communityFilter.value) : products.value,
)
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
  community: 'wuwa',
  monitor_enabled: true,
  monitor_keyword: '',
  monitor_interval_minutes: 30,
  monitor_min_views: 0,
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
    community: communityFilter.value || 'wuwa', monitor_enabled: true, monitor_keyword: '',
    monitor_interval_minutes: 30, monitor_min_views: 0,
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
    community: product.community || 'wuwa',
    monitor_enabled: product.monitor?.enabled ?? false,
    monitor_keyword: product.monitor?.keyword ?? '',
    monitor_interval_minutes: product.monitor?.interval_minutes ?? 30,
    monitor_min_views: product.monitor?.min_views ?? 0,
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
    community: form.community,
    monitor_enabled: form.monitor_enabled,
    monitor_keyword: form.monitor_keyword.trim() || null,
    monitor_interval_minutes: form.monitor_interval_minutes,
    monitor_min_views: form.monitor_min_views,
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
        },
      })
      toast.ok(form.monitor_enabled ? '商品与 B 站监控已创建' : '商品已创建')
    }
    resetForm()
    await refresh()
  } catch (exc: any) {
    error.value = `保存失败：${exc?.data?.detail ?? exc?.message ?? '请稍后重试'}`
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
    signals.value = [...data.items].sort((left, right) => {
      const candidateDiff =
        Number(right.review_status === 'candidate') - Number(left.review_status === 'candidate')
      if (candidateDiff) return candidateDiff
      // 证据不足的信号优先显示，保留人工纠错入口。
      const kindWeight = (s: ApiProductSignal) =>
        s.ai_hint?.kind === 'self_purchase' ? 1 : s.ai_hint?.kind === 'pr_sample' ? 0.5 : 0
      return kindWeight(right) - kindWeight(left)
    })
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
        新增时可按商品名称创建采集。履约信号持续自动分析，证据充分时自动采纳；此处保留人工纠错和确认。
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
          <input v-model="form.name" required class="input mt-1 w-full px-3 py-2 text-sm" placeholder="如：爱弥斯联名机械键盘">
        </label>
      </div>

      <label class="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 text-sm">
        <input v-model="form.monitor_enabled" type="checkbox" class="checkbox mt-0.5">
        <span>
          <span class="font-medium text-slate-700 dark:text-zinc-200">启用 B 站关键词采集</span>
          <span class="mt-0.5 block text-xs text-slate-500 dark:text-zinc-400">搜索词留空使用完整商品名；同一分区的同词监控可复用，跨分区需设置独立搜索词。</span>
        </span>
      </label>

      <details class="mt-4 rounded-lg border border-slate-900/10 p-3 dark:border-white/10" :open="!!editing">
        <summary class="cursor-pointer text-xs font-medium text-slate-500 dark:text-zinc-400">高级匹配与商品信息</summary>
        <div class="mt-3 grid gap-3 sm:grid-cols-3">
          <label class="text-xs text-slate-500 sm:col-span-2 dark:text-zinc-400">
            内容匹配词
            <input v-model="form.watch_terms" required class="input mt-1 w-full px-3 py-2 text-sm" placeholder="逗号分隔" @input="termsTouched = true">
            <span class="mt-1 block text-[11px] text-slate-400">已按商品名自动生成；避免改成角色名等过宽词。</span>
          </label>
          <select v-model="form.community" class="select px-3 py-2 text-sm" title="所属游戏分区：履约证据从对应分区的内容池匹配">
            <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">分区：{{ c.label }}</option>
          </select>
          <input v-model="form.slug" :disabled="!!editing" placeholder="URL 标识，留空自动生成" class="input px-3 py-2 text-sm disabled:opacity-50">
          <input v-model="form.publisher" placeholder="发行方" class="input px-3 py-2 text-sm">
          <input v-model="form.skus" placeholder="SKU（逗号分隔）" class="input px-3 py-2 text-sm">
          <input v-model="form.price_note" placeholder="价格说明" class="input px-3 py-2 text-sm">
          <input v-model="form.presale_at" placeholder="预售日期" class="input px-3 py-2 text-sm">
          <input v-model="form.balance_window" placeholder="尾款窗口" class="input px-3 py-2 text-sm">
          <input v-model="form.original_ship_at" placeholder="原定发货" class="input px-3 py-2 text-sm">
          <input v-model="form.ship_note" placeholder="发货说明" class="input px-3 py-2 text-sm">
          <template v-if="form.monitor_enabled || editing?.monitor">
            <label class="text-xs text-slate-500 dark:text-zinc-400">B 站搜索词
              <input v-model="form.monitor_keyword" maxlength="255" placeholder="留空用完整商品名" class="input mt-1 w-full px-3 py-2 text-sm">
            </label>
            <label class="text-xs text-slate-500 dark:text-zinc-400">采集间隔（分钟）
              <input v-model.number="form.monitor_interval_minutes" type="number" min="5" max="1440" step="1" required class="input mt-1 w-full px-3 py-2 text-sm">
            </label>
            <label class="text-xs text-slate-500 dark:text-zinc-400">最低播放量
              <input v-model.number="form.monitor_min_views" type="number" min="0" step="1" required class="input mt-1 w-full px-3 py-2 text-sm">
            </label>
          </template>
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
          <h2 class="text-sm font-semibold">履约信号：{{ signalProduct.name }}</h2>
          <p class="mt-1 text-xs text-slate-600 dark:text-zinc-400">系统自动采纳有充分依据的信号；证据不足时继续跟踪。可在此修正主体、获取方式及原文摘录，人工决定会单独标注。</p>
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
              <span class="chip" :class="signal.review_status === 'verified' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' : signal.review_status === 'candidate' ? 'bg-amber-500/10 text-amber-800 dark:text-amber-300' : signal.review_status === 'conflict' ? 'bg-red-500/10 text-red-700 dark:text-red-300' : 'bg-slate-500/10 text-slate-600 dark:text-zinc-400'">{{ productSignalReviewLabel(signal) }}</span>
              <span
                v-if="signal.review_status === 'candidate' && signal.ai_hint && signal.ai_hint.kind !== 'none'"
                class="chip bg-sky-500/10 text-sky-700 dark:text-sky-300"
                :title="signal.ai_hint.reason"
              >AI：{{ fulfillmentKindLabels[signal.ai_hint.kind] ?? signal.ai_hint.kind }} {{ signal.ai_hint.confidence }}%</span>
            </span>
            <span class="mt-2 block text-sm text-slate-700 dark:text-zinc-200">{{ signal.summary }}</span>
            <span v-if="signal.review_reason" class="mt-1 block text-xs leading-relaxed text-slate-600 dark:text-zinc-400">{{ signal.review_reason }}</span>
            <span class="mt-1 block text-xs text-slate-400">{{ signal.account || signal.platform }} · 原文 #{{ signal.content_id }}</span>
          </button>
        </div>

        <form v-if="editingSignal" class="rounded-lg border border-slate-900/10 p-4 dark:border-white/10" @submit.prevent="saveSignal">
          <p class="text-sm font-medium">审核 #{{ editingSignal.id }}</p>
          <div class="mt-3 grid gap-3">
            <label class="text-xs text-slate-500">审核状态
              <select v-model="signalForm.review_status" class="select mt-1 min-h-11 w-full px-3 text-sm">
                <option value="candidate">继续自动跟踪</option><option value="verified">人工确认</option><option value="rejected">驳回</option><option value="conflict">证据冲突</option>
              </select>
            </label>
            <div class="grid grid-cols-2 gap-3">
              <label class="text-xs text-slate-500">主体
                <select v-model="signalForm.actor_type" class="select mt-1 min-h-11 w-full px-2 text-sm">
                  <option value="unknown">待确认</option><option value="official">官方</option><option value="user">普通用户</option><option value="seeded">送测账号</option><option value="media">媒体</option><option value="merchant">商家</option>
                </select>
              </label>
              <label class="text-xs text-slate-500">获取方式
                <select v-model="signalForm.acquisition_type" class="select mt-1 min-h-11 w-full px-2 text-sm">
                  <option value="unknown">待确认</option><option value="self_purchase">自购</option><option value="seeded">送测</option>
                </select>
              </label>
            </div>
            <label class="text-xs text-slate-500">事实摘要
              <textarea v-model="signalForm.summary" rows="2" required class="textarea mt-1 w-full px-3 py-2 text-sm"></textarea>
            </label>
            <label class="text-xs text-slate-500">原文摘录
              <textarea v-model="signalForm.claim_quote" rows="3" class="textarea mt-1 w-full px-3 py-2 text-sm"></textarea>
            </label>
            <label class="text-xs text-slate-500">审核备注
              <textarea v-model="signalForm.note" rows="2" class="textarea mt-1 w-full px-3 py-2 text-sm"></textarea>
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

    <div class="mb-3 flex items-center gap-2">
      <select
        v-model="communityFilter"
        class="select px-3 py-2 text-sm"
      >
        <option value="">全部分区</option>
        <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
      </select>
    </div>
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
          <tr v-for="p in filteredProducts" :key="p.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="px-4 py-3">
              <span class="font-medium">{{ p.name }}</span>
              <span class="mono ml-2 text-xs text-slate-400">{{ p.slug }}</span>
              <span class="chip ml-1 whitespace-nowrap" :class="ADMIN_COMMUNITY_BADGE[p.community ?? ''] ?? 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'">{{ ADMIN_COMMUNITY_LABELS[p.community ?? ''] ?? '鸣潮' }}</span>
            </td>
            <td class="max-w-48 truncate px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.watch_terms.join(' / ') || '—' }}</td>
            <td class="max-w-48 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.fulfillment.official.state_label }}</td>
            <td class="max-w-52 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.fulfillment.consumer.state_label }}</td>
            <td class="max-w-48 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.fulfillment.quality.state_label }}</td>
            <td class="whitespace-nowrap px-4 py-3 text-right">
              <button class="min-h-11 rounded px-2 text-xs text-emerald-700 hover:underline dark:text-emerald-300" @click="openSignals(p)">查看 / 纠正信号<span v-if="p.fulfillment.candidate_total">（{{ p.fulfillment.candidate_total }}）</span></button>
              <button class="min-h-11 rounded px-2 text-xs text-blue-600 hover:underline dark:text-blue-300" @click="startEdit(p)">编辑</button>
              <button class="min-h-11 rounded px-2 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busy" @click="remove(p)">删除</button>
            </td>
          </tr>
          <tr v-if="!filteredProducts.length">
            <td colspan="6" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">暂无商品</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
