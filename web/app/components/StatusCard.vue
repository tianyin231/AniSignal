<script setup lang="ts">
import type { ApiProductList } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { fmtTime } from '~/utils/admin-types'
import { sortProductsBySignal } from '~/utils/product-utils'

const props = defineProps<{ mobile?: boolean, embedded?: boolean }>()
const { api } = usePublicApi()
const route = useRoute()
const { community } = useGameSection()
const { active } = useActiveChannel()
// 履约信号跟随页面视角：community=all（首页聚合）时展示全部分区商品；
// 其余页面跟随当前高亮的频道（详情页即内容所属分区），避免在星铁事件旁展示鸣潮商品
const scope = computed(() => {
  if (route.query.community === 'all' || (route.path === '/' && !route.query.community)) return 'all'
  return communityMeta(active.value) ? active.value! : community.value
})
const overviewTo = computed(() => ({ path: '/products', query: { community: scope.value } }))
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
const productCommunity = computed(() => communityMeta(product.value?.community))
useVisibleRefresh(() => refresh({ dedupe: 'defer' }), () =>
  status.value !== 'pending' && (props.embedded || window.matchMedia('(min-width: 1280px)').matches !== Boolean(props.mobile)),
)

const footLine = computed(() => {
  const f = product.value?.fulfillment
  if (!f) return ''
  return [
    f.automated_count ? `${f.automated_count} 条动态` : '',
    f.updated_at ? fmtTime(f.updated_at) : '',
  ].filter(Boolean).join(' · ')
})
</script>

<template>
  <!-- 当前分区没有追踪商品就整卡隐藏，不留空状态 -->
  <section v-if="!(status === 'success' && !products.length)" class="panel flex max-h-80 min-w-0 flex-col p-5">
    <div class="flex shrink-0 items-center justify-between gap-2">
      <h2 class="text-base font-bold text-ink">
        <NuxtLink :to="overviewTo" class="inline-flex min-h-11 items-center hover:text-brand-ink xl:min-h-0">履约追踪</NuxtLink>
      </h2>
      <NuxtLink :to="overviewTo" class="link inline-flex min-h-11 shrink-0 items-center text-[13px] xl:min-h-0">全部商品</NuxtLink>
    </div>
    <p v-if="error" role="alert" class="mt-2 shrink-0 text-xs text-amber-700 dark:text-amber-300">履约信号暂时更新失败。<button class="ml-1 underline" type="button" @click="refresh()">重试</button></p>
    <!-- 商品数量和长说明只影响卡片内的滚动区，不再撑高首页头图。 -->
    <div v-if="product" class="mt-2 min-h-0 overflow-y-auto overscroll-contain pr-1">
      <select
        v-if="products.length > 1"
        :value="product.slug"
        aria-label="选择追踪商品"
        class="select min-h-11 w-full min-w-0 truncate text-xs"
        @change="selectedSlug = ($event.target as HTMLSelectElement).value"
      >
        <option v-for="p in products" :key="p.slug" :value="p.slug">{{ p.name }}</option>
      </select>
      <NuxtLink
        :to="{ path: `/products/${product.slug}`, query: { community: scope } }"
        class="mt-2 flex items-start gap-2 text-[15px] font-semibold text-ink hover:text-brand-ink"
      >
        <span class="line-clamp-2 min-w-0 [overflow-wrap:anywhere]">{{ product.name }}</span>
        <span v-if="productCommunity" class="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold" :class="communityClasses(productCommunity.color).chip">{{ productCommunity.label }}</span>
      </NuxtLink>
      <div v-if="product.brief" class="mt-3">
        <ProductBrief :brief="product.brief" compact clamp-official />
      </div>
      <p v-if="product.brief?.archived" class="mt-3 text-xs text-muted">窗口已结束</p>
      <p v-if="footLine" class="mono mt-3 text-[11px] text-faint">{{ footLine }}</p>
    </div>
    <p v-else-if="status === 'pending'" class="mt-2 text-xs text-muted">正在加载商品…</p>
    <p v-else-if="!error" class="mt-2 text-xs leading-relaxed text-muted">当前分区暂无追踪中的商品。</p>
  </section>
</template>
