<script setup lang="ts">
import { Building2, ShieldAlert, Users } from 'lucide-vue-next'
import type { ApiProductList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const { api } = usePublicApi()
const { data } = await useAsyncData('status-product', () =>
  api<ApiProductList>('/api/products'),
)
const products = computed(() => data.value?.items ?? [])
// 多商品：默认展示第一个，可通过 chips 切换
const selectedSlug = ref<string | null>(null)
const product = computed(
  () => products.value.find((p) => p.slug === selectedSlug.value) ?? products.value[0],
)

const candidates = ref<{ id: number; summary: string; occurred_at: string | null }[]>([])
watch(product, async (p) => {
  if (!p?.slug) return
  try {
    const data = await api<{ items: { id: number; summary: string; review_status: string; occurred_at: string | null }[] }>(
      `/api/products/${p.slug}/signals`,
    )
    candidates.value = data.items
      .filter((s) => s.review_status === 'candidate')
      .slice(0, 2)
      .map((s) => ({ id: s.id, summary: s.summary, occurred_at: s.occurred_at }))
  } catch { /* 候选预览失败不影响主卡 */ }
}, { immediate: true })

const axisMeta = [
  { key: 'official', icon: Building2 },
  { key: 'consumer', icon: Users },
  { key: 'quality', icon: ShieldAlert },
] as const
</script>

<template>
  <section v-if="product" class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">履约信号</h2>
      <NuxtLink :to="`/products/${product.slug}`" class="link text-xs">详情</NuxtLink>
    </div>
    <!-- 多商品切换：更多商品上架后在这里选择 -->
    <div v-if="products.length > 1" class="mt-2 flex flex-wrap gap-1">
      <button
        v-for="p in products"
        :key="p.slug"
        type="button"
        class="max-w-full truncate rounded-md px-2 py-1 text-[11px] transition-colors"
        :class="p.slug === product.slug
          ? 'bg-blue-500/10 font-medium text-blue-700 dark:bg-blue-400/15 dark:text-blue-300'
          : 'text-slate-500 hover:bg-slate-200/70 dark:text-zinc-400 dark:hover:bg-zinc-800/70'"
        @click="selectedSlug = p.slug"
      >
        {{ p.name }}
      </button>
    </div>
    <p class="mt-1 truncate text-xs text-slate-400 dark:text-zinc-500">{{ product.name }}</p>
    <div class="mt-3 space-y-2">
      <div
        v-for="axis in axisMeta"
        :key="axis.key"
        class="rounded-lg border border-slate-900/5 bg-white/45 p-2.5 dark:border-white/10 dark:bg-white/5"
      >
        <div class="flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-zinc-300">
          <axis.icon :size="13" /> {{ product.fulfillment[axis.key].label }}
        </div>
        <p class="mt-1 text-xs font-medium text-slate-700 dark:text-zinc-200">{{ product.fulfillment[axis.key].state_label }}</p>
        <p
          v-if="product.fulfillment[axis.key].ai_preview"
          class="mt-1 text-[11px] leading-relaxed text-sky-600 dark:text-sky-400"
        >
          AI 预判：{{ product.fulfillment[axis.key].ai_preview }}
        </p>
        <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ product.fulfillment[axis.key].summary }}</p>
      </div>
    </div>
    <div class="mt-2 border-t border-slate-100 pt-2.5 dark:border-zinc-800">
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">{{ product.fulfillment.candidate_total }} 条待核验</span>
        <span class="mono text-[11px] text-slate-400">{{ fmtTime(product.updated_at) }}</span>
      </div>
      <ul v-if="candidates.length" class="mt-1.5 space-y-1">
        <li v-for="c in candidates" :key="c.id" class="truncate text-xs text-slate-500 dark:text-zinc-400">
          <span class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">未核验</span>
          {{ c.summary }}
        </li>
      </ul>
    </div>
  </section>
</template>
