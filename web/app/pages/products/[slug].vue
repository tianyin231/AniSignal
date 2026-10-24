<script setup lang="ts">
import type { ApiProduct, ApiProductSignalList } from '~/composables/usePublicApi'
import { GAME_COMMUNITIES, communityClasses } from '~/composables/useGameSection'

const route = useRoute()
const { api } = usePublicApi()

const productRequest = useAsyncData(
  () => `product-${route.params.slug}`,
  (_app, { signal }) => api<ApiProduct>(`/api/products/${route.params.slug}`, { signal }),
  { watch: [() => route.params.slug] },
)
const signalsRequest = useAsyncData(
  () => `product-signals-${route.params.slug}`,
  (_app, { signal }) => api<ApiProductSignalList>(`/api/products/${route.params.slug}/signals`, { signal }),
  { watch: [() => route.params.slug] },
)
const [
  { data: productData, error },
  { data: signalData, error: signalsError, refresh: refreshSignals },
] = await Promise.all([productRequest, signalsRequest])
if (error.value) throw createError({ statusCode: error.value.statusCode || 503, statusMessage: error.value.statusCode === 404 ? '商品不存在' : '商品暂时加载失败，请稍后重试', fatal: true })
if (!productData.value) throw createError({ statusCode: 404, statusMessage: '商品不存在', fatal: true })
const lastProduct = shallowRef(productData.value!)
watch(productData, value => { if (value) lastProduct.value = value })
const product = computed(() => productData.value ?? lastProduct.value)
const productCommunity = computed(() => product.value.community || 'wuwa')
const communityMeta = computed(() => GAME_COMMUNITIES.find(item => item.key === productCommunity.value))
// 分享直达时使用商品自身分区，聚合列表传来的 all 视角则保留。
if (route.query.community !== 'all' && route.query.community !== productCommunity.value) {
  await navigateTo({ path: route.path, query: { ...route.query, community: productCommunity.value } }, { replace: true })
}
const listFallback = computed(() => `/products?community=${route.query.community === 'all' ? 'all' : productCommunity.value}`)
const hasImages = computed(() => (product.value.images?.length ?? 0) > 0)
const signals = computed(() => signalData.value?.items ?? [])
useVisibleRefresh(() => Promise.all([
  productRequest.refresh({ dedupe: 'defer' }),
  signalsRequest.refresh({ dedupe: 'defer' }),
]))

useSeoMeta({ title: () => `${product.value.name} · 商品` })
</script>

<template>
  <div class="min-w-0 space-y-4 [overflow-wrap:anywhere]">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <BackButton :fallback="listFallback" label="返回商品" />
      <NuxtLink :to="listFallback" class="link inline-flex min-h-11 items-center text-sm">商品总览 →</NuxtLink>
    </div>
    <p v-if="error" role="alert" class="text-sm text-amber-700 dark:text-amber-300">商品状态暂时更新失败，当前显示上次结果。<button type="button" class="ml-1 underline" @click="productRequest.refresh()">重试</button></p>

    <!-- 无官方图时收起画廊单列排版，标题与履约信息直接占满首屏 -->
    <section class="panel grid items-start gap-6 p-4 sm:p-6 lg:gap-10" :class="hasImages ? 'md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]' : ''">
      <ProductGallery v-if="hasImages" :images="product.images || []" :name="product.name" :kind="product.product_type" :source-url="product.images_source_url" />
      <div class="min-w-0 py-1 md:py-3" :class="hasImages ? '' : 'sm:py-2'">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="mb-3 flex flex-wrap items-center gap-2"><span v-if="communityMeta" class="chip text-xs" :class="communityClasses(communityMeta.color).chip">{{ communityMeta.label }}</span><span v-if="product.product_type" class="text-xs text-muted">{{ product.product_type }}</span></div>
          <h1 class="text-2xl font-bold leading-tight tracking-tight text-ink sm:text-3xl lg:text-4xl">{{ product.name }}</h1>
          <p v-if="product.publisher || product.skus.length" class="mt-0.5 text-xs text-slate-600 dark:text-zinc-400">
            {{ [product.publisher, product.skus.join(' / ')].filter(Boolean).join(' · ') }}
          </p>
        </div>
        <span class="chip shrink-0">{{ product.monitor_status === 'archived' ? '已归档' : '追踪中' }}</span>
      </div>
      <p v-if="product.price_note" class="num mt-5 text-2xl font-semibold text-ink">{{ product.price_note }}</p>
      <div v-if="product.brief" class="mt-6 rounded-xl border border-line-soft p-4 sm:p-5">
        <ProductBrief :brief="product.brief" compact />
      </div>
      <p v-if="product.source_url" class="mt-3 text-xs">
        <a :href="product.source_url" class="link" target="_blank" rel="noopener noreferrer">公告来源</a>
      </p>
      </div>
    </section>

    <section class="panel p-4 sm:p-5">
      <h2 class="text-base font-bold text-ink">追踪动态（{{ signals.length }}）</h2>
      <div class="mt-4">
        <p v-if="signalsError" role="alert" class="text-sm text-amber-700 dark:text-amber-300">信号暂时加载失败。<button type="button" class="ml-1 underline" @click="refreshSignals()">重试</button></p>
        <ProductSignalList v-else :items="signals" :community="productCommunity" />
      </div>
    </section>
  </div>
</template>
