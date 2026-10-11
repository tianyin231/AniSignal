<script setup lang="ts">
import { Building2, ShieldAlert, Users } from 'lucide-vue-next'
import type { ApiProductList } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { sortProductsBySignal } from '~/utils/product-utils'

const props = defineProps<{ mobile?: boolean }>()
const { api } = usePublicApi()
const route = useRoute()
const { community } = useGameSection()
// 履约信号跟随页面视角：community=all（首页聚合）时展示全部分区商品
const scope = computed(() =>
  route.query.community === 'all' || (route.path === '/' && !route.query.community) ? 'all' : community.value,
)
const fetchedAt = useState<Record<string, number>>('status-products-fetched-at', () => ({}))
const { data, error, status, refresh } = await useLazyAsyncData(
  () => `status-product-${scope.value}`,
  async (_app, { signal }) => {
    const key = `status-product-${scope.value}`
    const result = await api<ApiProductList>('/api/products', { query: { community: scope.value }, signal })
    fetchedAt.value[key] = Date.now()
    return result
  },
  {
    dedupe: 'defer',
    // 桌面与移动端卡片同时挂载，只共享短期结果，不阻止手动刷新。
    getCachedData: (key, app, { cause }) => {
      if (cause === 'refresh:manual' || cause === 'refresh:hook') return
      if (app.isHydrating || Date.now() - (fetchedAt.value[key] || 0) < 30_000) return app.payload.data[key]
    },
  },
)
const products = computed(() => sortProductsBySignal(data.value?.items ?? []))
// 默认展示最近有信号变化的商品；详情页优先当前商品。
const selectedSlug = ref<string | null>(null)
watch([scope, () => route.params.slug], () => { selectedSlug.value = null })
const product = computed(
  () => products.value.find((p) => p.slug === (selectedSlug.value || route.params.slug)) ?? products.value[0],
)
useVisibleRefresh(() => refresh({ dedupe: 'defer' }), () =>
  status.value !== 'pending' && window.matchMedia('(min-width: 1280px)').matches !== Boolean(props.mobile),
)

// 右栏只做粗略速览：三轴状态行 + 待核验数，细节进详情页
const axisMeta = [
  { key: 'official', icon: Building2 },
  { key: 'consumer', icon: Users },
  { key: 'quality', icon: ShieldAlert },
] as const
</script>

<template>
  <section v-if="product || error" class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">履约信号</h2>
      <NuxtLink v-if="product" :to="{ path: `/products/${product.slug}`, query: { community: scope } }" class="link text-xs">详情</NuxtLink>
    </div>
    <p v-if="error" role="alert" class="mt-2 text-xs text-amber-700 dark:text-amber-300">履约信号暂时更新失败。<button class="ml-1 underline" type="button" @click="refresh()">重试</button></p>
    <template v-if="product">
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
      <span v-if="product.fulfillment.updated_at" class="mono text-[11px] text-slate-400">信号更新 {{ fmtTime(product.fulfillment.updated_at) }}</span>
    </div>
    </template>
  </section>
</template>
