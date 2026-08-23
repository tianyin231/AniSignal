<script setup lang="ts">
import { Building2, Users } from 'lucide-vue-next'
import type { ApiProductList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const { api } = usePublicApi()
const { data } = await useAsyncData('status-product', () =>
  api<ApiProductList>('/api/products'),
)
const product = computed(() => data.value?.items?.[0])
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
          <Building2 :size="13" /> 官方进度
        </div>
        <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ product.official_status }}</p>
        <p class="mono mt-1 text-[11px] text-slate-400">{{ product.official_evidence_count }} 条官方证据</p>
      </div>
      <div class="rounded-lg border border-slate-900/5 bg-white/45 p-2.5 dark:border-white/10 dark:bg-white/5">
        <div class="flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-zinc-300">
          <Users :size="13" /> 消费者样本
        </div>
        <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ product.consumer_status }}</p>
        <p class="mono mt-1 text-[11px] text-slate-400">{{ product.consumer_evidence_count }} 个已核验账号</p>
      </div>
    </div>
    <div class="mt-2 border-t border-slate-100 pt-2.5 dark:border-zinc-800">
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ product.stage_label }}</span>
        <span class="text-[11px] text-slate-400">{{ product.stage_source === 'manual' ? '人工审核' : product.stage_source === 'ai' ? 'AI 证据判断' : product.stage_source === 'automatic' ? '证据自动计算' : '未形成结论' }}</span>
      </div>
      <p class="mt-1.5 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">{{ product.stage_reason }}</p>
      <p class="mono mt-1 text-[11px] text-slate-400">更新于 {{ fmtTime(product.stage_updated_at || product.updated_at) }}</p>
    </div>
  </section>
</template>
