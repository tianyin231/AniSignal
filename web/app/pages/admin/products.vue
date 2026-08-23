<script setup lang="ts">
import type { ApiProduct, ApiProductList } from '~/composables/usePublicApi'
import { categoryMeta } from '~/utils/public-utils'

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

const reviewing = ref<ApiProduct | null>(null)
const review = reactive({ stage: 1, reason: '', evidence_ids: [] as number[] })
const reviewEvidences = computed(() =>
  (reviewing.value?.evidences ?? []).filter((item) => ['unboxing', 'arrived'].includes(item.category ?? '')),
)

const identityLabel: Record<string, string> = {
  official: '官方',
  seeded: '送测',
  user: '普通用户',
  unknown: '身份待核验',
  media: '媒体',
  merchant: '商家',
}

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
    if (reviewing.value)
      reviewing.value = data.items.find((item) => item.id === reviewing.value?.id) ?? null
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

function startReview(product: ApiProduct) {
  reviewing.value = product
  review.stage = product.stage_hint >= 1 && product.stage_hint <= 3 ? product.stage_hint : 1
  review.reason = product.stage_source === 'manual' ? product.stage_reason : ''
  review.evidence_ids = product.stage_source === 'manual'
    ? [...product.stage_evidence_ids]
    : []
}

async function saveReview() {
  if (!reviewing.value || !review.reason.trim() || !review.evidence_ids.length) return
  busy.value = true
  error.value = null
  try {
    await api(`/api/admin/products/${reviewing.value.id}`, {
      method: 'PUT',
      body: {
        stage_hint: review.stage,
        stage_hint_reason: review.reason.trim(),
        stage_hint_evidence_ids: review.evidence_ids,
      },
    })
    toast.ok('履约阶段审核已保存')
    reviewing.value = null
    await refresh()
  } catch (exc: any) {
    error.value = `审核失败：${exc?.data?.detail ?? exc?.message}`
    toast.err(error.value)
  } finally {
    busy.value = false
  }
}

async function restoreAutomatic() {
  if (!reviewing.value) return
  busy.value = true
  try {
    await api(`/api/admin/products/${reviewing.value.id}`, { method: 'PUT', body: { stage_hint: -1 } })
    toast.ok('已恢复按已核验证据自动计算')
    reviewing.value = null
    await refresh()
  } catch (exc: any) {
    toast.err(`恢复失败：${exc?.data?.detail ?? exc?.message}`)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-lg font-semibold">商品管理</h1>
      <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
        新增时只需名称和监控词；没有证据时系统保持“暂无结论”，人工阶段必须绑定原文并写明理由。
      </p>
    </div>

    <form class="panel p-5" @submit.prevent="submit">
      <div class="flex items-center justify-between gap-3">
        <h2 class="text-sm font-semibold">{{ editing ? `编辑：${editing.name}` : '快速新增商品' }}</h2>
        <button v-if="editing" type="button" class="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-zinc-300" @click="resetForm">取消编辑</button>
      </div>
      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          商品名称 <span class="text-red-500">*</span>
          <input v-model="form.name" required class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="如：爱弥斯联名机械键盘">
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          内容匹配词 <span class="text-red-500">*</span>
          <input v-model="form.watch_terms" required class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="逗号分隔" @input="termsTouched = true">
          <span class="mt-1 block text-[11px] text-slate-400">用于标题与正文匹配；系统会按商品名给出初始建议。</span>
        </label>
      </div>

      <label v-if="!editing" class="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 text-sm">
        <input v-model="form.create_bilibili_target" type="checkbox" class="mt-0.5 accent-blue-600">
        <span>
          <span class="font-medium text-slate-700 dark:text-zinc-200">同时创建 B 站关键词监控</span>
          <span class="mt-0.5 block text-xs text-slate-500 dark:text-zinc-400">默认每 30 分钟采集；已有同名监控会直接复用。</span>
        </span>
      </label>

      <details class="mt-4 rounded-lg border border-slate-900/10 p-3 dark:border-white/10" :open="!!editing">
        <summary class="cursor-pointer text-xs font-medium text-slate-500 dark:text-zinc-400">高级信息（选填）</summary>
        <div class="mt-3 grid gap-3 sm:grid-cols-3">
          <input v-model="form.slug" :disabled="!!editing" placeholder="URL 标识，留空自动生成" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm disabled:opacity-50 dark:border-white/15 dark:bg-white/5">
          <input v-model="form.publisher" placeholder="发行方" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.skus" placeholder="SKU（逗号分隔）" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.price_note" placeholder="价格说明" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.presale_at" placeholder="预售日期" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.balance_window" placeholder="尾款窗口" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.original_ship_at" placeholder="原定发货" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-model="form.ship_note" placeholder="发货说明" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
          <input v-if="!editing && form.create_bilibili_target" v-model="form.monitor_keyword" placeholder="B站搜索词，留空用首个匹配词" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
      </details>

      <div class="mt-4 flex items-center gap-3">
        <button type="submit" class="btn-primary px-4 py-2" :disabled="busy || !form.name.trim() || !form.watch_terms.trim()">
          {{ busy ? '处理中…' : editing ? '保存修改' : '创建并回看历史内容' }}
        </button>
        <p v-if="error" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
      </div>
    </form>

    <section v-if="reviewing" class="panel p-5">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold">审核履约阶段：{{ reviewing.name }}</h2>
          <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">只列出已关联的到货/开箱原文；阶段 4/5 缺少购买时间样本，暂不可确认。</p>
        </div>
        <button class="text-xs text-slate-400 hover:text-slate-600" @click="reviewing = null">关闭</button>
      </div>
      <div class="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <div>
          <label class="text-xs text-slate-500">确认阶段</label>
          <select v-model.number="review.stage" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white px-3 py-2 text-sm dark:border-white/15 dark:bg-zinc-900">
            <option :value="1">出现送测样品</option>
            <option :value="2">首位普通用户到货</option>
            <option :value="3">多名独立用户到货</option>
          </select>
          <label class="mt-3 block text-xs text-slate-500">审核理由</label>
          <textarea v-model="review.reason" rows="4" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="说明如何判断账号身份、商品对应关系与到货事实"></textarea>
        </div>
        <div>
          <p class="text-xs text-slate-500">选择原始证据（{{ review.evidence_ids.length }}）</p>
          <div v-if="reviewEvidences.length" class="mt-1.5 max-h-64 space-y-2 overflow-y-auto">
            <label v-for="item in reviewEvidences" :key="item.content_id" class="flex cursor-pointer gap-2 rounded-lg border border-slate-900/10 p-2.5 hover:bg-slate-500/5 dark:border-white/10">
              <input v-model="review.evidence_ids" type="checkbox" :value="item.content_id" class="mt-0.5 accent-blue-600">
              <span class="min-w-0">
                <span class="block truncate text-sm text-slate-700 dark:text-zinc-200">{{ item.title || `内容 #${item.content_id}` }}</span>
                <span class="mt-0.5 block text-xs text-slate-400">{{ item.account || item.platform }} · {{ identityLabel[item.identity] || item.identity }} · {{ categoryMeta[item.category || '']?.label || item.category }}</span>
              </span>
            </label>
          </div>
          <p v-else class="mt-1.5 rounded-lg border border-dashed border-slate-900/15 p-4 text-sm text-slate-400 dark:border-white/15">暂无关联的到货/开箱证据，不能人工确认阶段。</p>
        </div>
      </div>
      <div class="mt-4 flex flex-wrap gap-2">
        <button class="btn-primary px-4 py-2" :disabled="busy || !review.reason.trim() || !review.evidence_ids.length" @click="saveReview">保存审核结论</button>
        <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm text-slate-500 hover:bg-slate-500/5 dark:border-white/15 dark:text-zinc-400" :disabled="busy" @click="restoreAutomatic">恢复自动计算</button>
      </div>
    </section>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-[900px] text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">商品</th>
            <th class="px-3 py-2.5 font-medium">监控词</th>
            <th class="px-3 py-2.5 font-medium">官方进度</th>
            <th class="px-3 py-2.5 font-medium">消费者样本</th>
            <th class="px-3 py-2.5 font-medium">履约阶段</th>
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
            <td class="max-w-48 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.official_status }}</td>
            <td class="max-w-52 px-3 py-3 text-xs text-slate-500 dark:text-zinc-400">{{ p.consumer_status }}</td>
            <td class="px-3 py-3">
              <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ p.stage_label }}</span>
              <span class="mt-1 block text-[11px] text-slate-400">{{ p.stage_source === 'manual' ? '人工审核' : p.stage_source === 'automatic' ? '自动计算' : '未形成结论' }}</span>
            </td>
            <td class="whitespace-nowrap px-4 py-3 text-right">
              <button class="rounded px-2 py-1 text-xs text-emerald-700 hover:underline dark:text-emerald-300" @click="startReview(p)">审核阶段</button>
              <button class="rounded px-2 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" @click="startEdit(p)">编辑</button>
              <button class="rounded px-2 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busy" @click="remove(p)">删除</button>
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
