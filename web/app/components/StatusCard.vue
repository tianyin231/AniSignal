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
    f.automated_count ? `${f.automated_count} 条已采纳` : '',
    f.candidate_total ? `${f.candidate_total} 条跟踪中` : '',
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
      <NuxtLink :to="overviewTo" class="link inline-flex min-h-11 shrink-0 items-center text-[13px] xl:min-h-0">全部商品</NuxtLink>
    </div>
    <p v-if="error" role="alert" class="mt-2 text-xs text-amber-700 dark:text-amber-300">履约信号暂时更新失败。<button class="ml-1 underline" type="button" @click="refresh()">重试</button></p>
    <template v-if="product">
      <!-- 多商品按最近信号更新排序，切换只影响当前预览。 -->
      <div v-if="products.length > 1" class="mt-2 flex flex-wrap gap-1">
        <button
          v-for="p in products"
          :key="p.slug"
          type="button"
          class="min-h-9 max-w-full truncate rounded-lg px-2.5 py-1 text-xs transition-colors"
          :aria-pressed="p.slug === product.slug"
          :class="p.slug === product.slug ? 'bg-brand-soft font-semibold text-brand-ink' : 'text-muted hover:bg-chip'"
          @click="selectedSlug = p.slug"
        >
          {{ p.name }}
        </button>
      </div>
      <NuxtLink
        :to="{ path: `/products/${product.slug}`, query: { community: scope } }"
        class="mt-2 flex flex-wrap items-center gap-2 text-[15px] font-semibold text-ink hover:text-brand-ink [overflow-wrap:anywhere]"
      >
        {{ product.name }}
        <span v-if="productCommunity" class="rounded-md px-1.5 py-0.5 text-[11px] font-semibold" :class="communityClasses(productCommunity.color).chip">{{ productCommunity.label }}</span>
      </NuxtLink>
      <div v-if="product.brief" class="mt-4">
        <ProductBrief :brief="product.brief" />
      </div>
      <p v-if="product.brief?.archived" class="mt-3 text-xs text-muted">窗口已结束</p>
      <p v-if="footLine" class="mono mt-4 text-[11px] text-faint">{{ footLine }}</p>
    </template>
    <p v-else-if="status === 'pending'" class="mt-2 text-xs text-muted">正在加载商品…</p>
    <p v-else-if="!error" class="mt-2 text-xs leading-relaxed text-muted">当前分区暂无追踪中的商品。</p>
  </section>
</template>
