<script setup lang="ts">
import type { ApiProduct, ApiProductList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/public-utils'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '商品管理 · AniSignal 管理看板' })

const { api } = useAdminApi()
const products = ref<ApiProduct[]>([])
const error = ref<string | null>(null)
const busy = ref(false)

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
})
const editing = ref<ApiProduct | null>(null)

async function refresh() {
  try {
    const data = await api<ApiProductList>('/api/admin/products')
    products.value = data.items
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}
onMounted(refresh)

function startEdit(product: ApiProduct) {
  editing.value = product
  form.slug = product.slug
  form.name = product.name
  form.publisher = product.publisher ?? ''
  form.skus = product.skus.join(', ')
  form.price_note = product.price_note ?? ''
  form.presale_at = product.presale_at ?? ''
  form.balance_window = product.balance_window ?? ''
  form.original_ship_at = product.original_ship_at ?? ''
  form.ship_note = product.ship_note ?? ''
  form.watch_terms = product.watch_terms.join(', ')
}

async function submit() {
  if (!form.name.trim() || !form.slug.trim()) return
  busy.value = true
  error.value = null
  const body = {
    name: form.name.trim(),
    publisher: form.publisher || null,
    skus: form.skus.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    price_note: form.price_note || null,
    presale_at: form.presale_at || null,
    balance_window: form.balance_window || null,
    original_ship_at: form.original_ship_at || null,
    ship_note: form.ship_note || null,
    watch_terms: form.watch_terms.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
  }
  try {
    if (editing.value) {
      await api(`/api/admin/products/${editing.value.id}`, { method: 'PUT', body })
    } else {
      await api('/api/admin/products', { method: 'POST', body: { slug: form.slug.trim(), ...body } })
    }
    editing.value = null
    form.slug = form.name = form.publisher = form.skus = form.price_note = ''
    form.presale_at = form.balance_window = form.original_ship_at = form.ship_note = form.watch_terms = ''
    await refresh()
  } catch (exc: any) {
    error.value = exc?.statusCode === 409 ? 'slug 已存在' : `保存失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
  }
}

async function remove(product: ApiProduct) {
  if (!confirm(`删除商品「${product.name}」？关联事件会保留但失去商品关联。`)) return
  busy.value = true
  try {
    await api(`/api/admin/products/${product.id}`, { method: 'DELETE' })
    await refresh()
  } finally {
    busy.value = false
  }
}

async function bumpStage(product: ApiProduct, delta: number) {
  const next = Math.max(-1, Math.min(5, product.current_stage + delta))
  busy.value = true
  try {
    await api(`/api/admin/products/${product.id}`, { method: 'PUT', body: { stage_hint: next } })
    await refresh()
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
        商品档案与监控词（watch_terms）；履约阶梯默认由引擎按证据推进，手动指定后以指定为准（-1 恢复自动）。
      </p>
    </div>

    <form class="panel grid gap-3 p-5 sm:grid-cols-3" @submit.prevent="submit">
      <div class="sm:col-span-3 flex items-center justify-between">
        <h2 class="text-sm font-semibold">{{ editing ? `编辑：${editing.name}` : '新增商品' }}</h2>
        <button v-if="editing" type="button" class="text-xs text-slate-400 hover:underline" @click="editing = null">取消编辑</button>
      </div>
      <input v-model="form.slug" :disabled="!!editing" placeholder="slug（URL 标识，如 aymiss-keyboard）" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm disabled:opacity-50 dark:border-white/15 dark:bg-white/5">
      <input v-model="form.name" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="商品名称 *">
      <input v-model="form.publisher" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="发行方">
      <input v-model="form.skus" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="SKU（逗号分隔）">
      <input v-model="form.price_note" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="价格说明（如 ¥749 起）">
      <input v-model="form.presale_at" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="预售日期">
      <input v-model="form.balance_window" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="尾款窗口">
      <input v-model="form.original_ship_at" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" placeholder="原定发货">
      <input v-model="form.watch_terms" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm sm:col-span-2 dark:border-white/15 dark:bg-white/5" placeholder="监控词（逗号分隔，如 爱弥斯,联名键盘）*">
      <input v-model="form.ship_note" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm sm:col-span-2 dark:border-white/15 dark:bg-white/5" placeholder="发货说明">
      <button type="submit" class="btn-primary px-4 py-2" :disabled="busy || !form.name.trim() || (!editing && !form.slug.trim())">
        {{ editing ? '保存修改' : '新增商品' }}
      </button>
      <p v-if="error" class="sm:col-span-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    </form>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-2xl text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">商品</th>
            <th class="px-3 py-2.5 font-medium">监控词</th>
            <th class="px-3 py-2.5 font-medium">当前阶段</th>
            <th class="px-3 py-2.5 font-medium">更新</th>
            <th class="px-4 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in products" :key="p.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="px-4 py-2.5">
              <span class="font-medium">{{ p.name }}</span>
              <span class="mono ml-2 text-xs text-slate-400">{{ p.slug }}</span>
            </td>
            <td class="max-w-48 truncate px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">{{ p.watch_terms.join(' / ') ?? '—' }}</td>
            <td class="px-3 py-2.5">
              <div class="flex items-center gap-1.5">
                <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ p.stage_label }}</span>
                <button class="rounded px-1 text-xs text-slate-400 hover:underline" :disabled="busy" @click="bumpStage(p, -1)">−</button>
                <button class="rounded px-1 text-xs text-slate-400 hover:underline" :disabled="busy" @click="bumpStage(p, 1)">＋</button>
              </div>
            </td>
            <td class="mono whitespace-nowrap px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">{{ fmtTime(p.updated_at) }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <button class="rounded px-2 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" @click="startEdit(p)">编辑</button>
              <button class="rounded px-2 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busy" @click="remove(p)">删除</button>
            </td>
          </tr>
          <tr v-if="!products.length">
            <td colspan="5" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">暂无商品</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
