<script setup lang="ts">
import { Building2, ShieldAlert, Users } from 'lucide-vue-next'
import type { ApiProductList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const { api } = usePublicApi()
const { data } = await useAsyncData('status-product', () =>
  api<ApiProductList>('/api/products'),
)
const product = computed(() => data.value?.items?.[0])

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
})
</script>

<template>
  <section v-if="product" class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">履约信号</h2>
      <NuxtLink :to="`/products/${product.slug}`" class="link text-xs">详情</NuxtLink>
    </div>
    <p class="mt-1 truncate text-xs text-slate-400 dark:text-zinc-500">{{ product.name }}</p>
    <div class="mt-3 space-y-2">
      <div class="rounded-lg border border-slate-900/5 bg-white/45 p-2.5 dark:border-white/10 dark:bg-white/5">
        <div class="flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-zinc-300">
          <Building2 :size="13" /> {{ product.fulfillment.official.label }}
        </div>
        <p class="mt-1 text-xs font-medium text-slate-700 dark:text-zinc-200">{{ product.fulfillment.official.state_label }}</p>
        <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ product.fulfillment.official.summary }}</p>
      </div>
      <div class="rounded-lg border border-slate-900/5 bg-white/45 p-2.5 dark:border-white/10 dark:bg-white/5">
        <div class="flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-zinc-300">
          <Users :size="13" /> {{ product.fulfillment.consumer.label }}
        </div>
        <p class="mt-1 text-xs font-medium text-slate-700 dark:text-zinc-200">{{ product.fulfillment.consumer.state_label }}</p>
        <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ product.fulfillment.consumer.summary }}</p>
      </div>
      <div class="rounded-lg border border-slate-900/5 bg-white/45 p-2.5 dark:border-white/10 dark:bg-white/5">
        <div class="flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-zinc-300">
          <ShieldAlert :size="13" /> {{ product.fulfillment.quality.label }}
        </div>
        <p class="mt-1 text-xs font-medium text-slate-700 dark:text-zinc-200">{{ product.fulfillment.quality.state_label }}</p>
        <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ product.fulfillment.quality.summary }}</p>
      </div>
    </div>
    <div v-if="candidates.length" class="mt-2.5 border-t border-slate-100 pt-2.5 dark:border-zinc-800">
      <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">最新候选信号（未核验）</p>
      <ul class="mt-1.5 space-y-1">
        <li v-for="c in candidates" :key="c.id" class="truncate text-xs text-slate-500 dark:text-zinc-400">
          <span class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">未核验</span>
          {{ c.summary }}
        </li>
      </ul>
    </div>
    <div class="mt-2 border-t border-slate-100 pt-2.5 dark:border-zinc-800">
      <div class="flex flex-wrap items-center gap-1.5">
        <span v-if="product.fulfillment.candidate_total" class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">{{ product.fulfillment.candidate_total }} 条待核验</span>
        <span class="mono text-[11px] text-slate-400">{{ fmtTime(product.fulfillment.last_verified_at || product.updated_at) }}</span>
      </div>
    </div>
  </section>
</template>
