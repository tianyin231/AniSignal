<script setup lang="ts">
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
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
const productIndex = computed(() => products.value.findIndex(p => p.slug === product.value?.slug))
function stepProduct(direction: number) {
  const index = (productIndex.value + direction + products.value.length) % products.value.length
  selectedSlug.value = products.value[index]!.slug
}
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
  <section v-if="!(status === 'success' && !products.length)" class="panel min-w-0 p-5">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-base font-bold text-ink">
        <NuxtLink :to="overviewTo" class="inline-flex min-h-11 items-center hover:text-brand-ink xl:min-h-0">履约追踪</NuxtLink>
      </h2>
      <div class="flex items-center gap-2">
        <div v-if="products.length > 1" class="flex items-center" role="group" aria-label="切换追踪商品">
          <button type="button" aria-label="上一件商品" class="grid size-11 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-chip hover:text-ink" @click="stepProduct(-1)">
            <ChevronLeft :size="16" aria-hidden="true" />
          </button>
          <span class="mono min-w-8 text-center text-[11px] text-faint" aria-live="polite">{{ productIndex + 1 }}/{{ products.length }}</span>
          <button type="button" aria-label="下一件商品" class="grid size-11 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-chip hover:text-ink" @click="stepProduct(1)">
            <ChevronRight :size="16" aria-hidden="true" />
          </button>
        </div>
        <NuxtLink :to="overviewTo" class="link inline-flex min-h-11 shrink-0 items-center text-[13px] xl:min-h-0">全部商品</NuxtLink>
      </div>
    </div>
    <p v-if="error" role="alert" class="mt-2 text-xs text-amber-700 dark:text-amber-300">履约信号暂时更新失败。<button class="ml-1 underline" type="button" @click="refresh()">重试</button></p>
    <!-- 首页只预览一件商品的摘要；完整说明通过商品链接查看。 -->
    <div v-if="product" class="mt-2">
      <NuxtLink
        :to="{ path: `/products/${product.slug}`, query: { community: scope } }"
        :title="product.name"
        class="flex items-start gap-2 text-[15px] font-semibold text-ink hover:text-brand-ink"
      >
        <span class="line-clamp-2 min-w-0 [overflow-wrap:anywhere]">{{ product.name }}</span>
        <span v-if="productCommunity" class="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold" :class="communityClasses(productCommunity.color).chip">{{ productCommunity.label }}</span>
      </NuxtLink>
      <div v-if="product.brief" class="mt-3">
        <ProductBrief :brief="product.brief" compact preview />
      </div>
      <div v-if="product.brief?.archived || footLine" class="mt-3 flex items-center gap-2 text-[11px] text-faint">
        <span v-if="product.brief?.archived" class="shrink-0 text-muted">窗口已结束</span>
        <p v-if="footLine" class="mono truncate">{{ footLine }}</p>
      </div>
    </div>
    <p v-else-if="status === 'pending'" class="mt-2 text-xs text-muted">正在加载商品…</p>
    <p v-else-if="!error" class="mt-2 text-xs leading-relaxed text-muted">当前分区暂无追踪中的商品。</p>
  </section>
</template>
