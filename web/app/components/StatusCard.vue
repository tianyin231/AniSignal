<script setup lang="ts">
import { Building2, ShieldAlert, Users } from 'lucide-vue-next'
import type { ApiProductList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const { api } = usePublicApi()
const route = useRoute()
const { community } = useGameSection()
// 履约信号跟随页面视角：community=all（首页聚合）时展示全部分区商品
const scope = computed(() =>
  String(route.query.community || '') === 'all' ? 'all' : community.value,
)
const { data } = await useLazyAsyncData(
  () => `status-product-${scope.value}`,
  () => api<ApiProductList>('/api/products', { query: { community: scope.value } }),
  { watch: [scope] },
)
const products = computed(() => data.value?.items ?? [])
// 多商品：默认展示第一个，可通过 chips 切换
const selectedSlug = ref<string | null>(null)
const product = computed(
  () => products.value.find((p) => p.slug === selectedSlug.value) ?? products.value[0],
)

// 右栏只做粗略速览：三轴状态行 + 待核验数，细节进详情页
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
    <div class="mt-3 space-y-1.5">
      <div
        v-for="axis in axisMeta"
        :key="axis.key"
        class="flex items-center justify-between gap-2 rounded-lg border border-slate-900/5 bg-white/45 px-2.5 py-2 dark:border-white/10 dark:bg-white/5"
      >
        <span class="flex shrink-0 items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-zinc-300">
          <component :is="axis.icon" :size="13" /> {{ product.fulfillment[axis.key].label }}
        </span>
        <span class="truncate text-xs font-medium text-slate-700 dark:text-zinc-200">{{ product.fulfillment[axis.key].state_label }}</span>
      </div>
    </div>
    <div class="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2.5 dark:border-zinc-800">
      <span class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">{{ product.fulfillment.candidate_total }} 条待核验</span>
      <span class="mono text-[11px] text-slate-400">{{ fmtTime(product.updated_at) }}</span>
    </div>
  </section>
</template>
