<script setup lang="ts">
import type { ApiEventList, ApiProduct, ApiProductSignalList } from '~/composables/usePublicApi'
import { toAppEvent } from '~/utils/public-utils'
import { GAME_COMMUNITIES, communityClasses } from '~/composables/useGameSection'

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()

const productRequest = useAsyncData(
  () => `product-${route.params.slug}`,
  (_app, { signal }) => api<ApiProduct>(`/api/products/${route.params.slug}`, { signal }),
  { watch: [() => route.params.slug] },
)
const tabs = [
  { key: 'overview', label: '总览' },
  { key: 'timeline', label: '事件时间线' },
  { key: 'evidence', label: '信号证据' },
]
const view = computed({
  get: () => String(route.query.view || 'overview'),
  set: (value: string) => router.replace({ query: { ...route.query, view: value } }),
})

const eventsRequest = useAsyncData(
  () => `product-events-${route.params.slug}`,
  (_app, { signal }) => api<ApiEventList>('/api/events', { query: { community: 'all', product_slug: route.params.slug, limit: 50 }, signal }),
  { watch: [() => route.params.slug] },
)
const signalsRequest = useAsyncData(
  () => `product-signals-${route.params.slug}`,
  (_app, { signal }) => api<ApiProductSignalList>(`/api/products/${route.params.slug}/signals`, { signal }),
  { watch: [() => route.params.slug] },
)
const [
  { data: productData, error },
  { data: eventsData, error: eventsError, refresh: refreshEvents },
  { data: signalData, error: signalsError, refresh: refreshSignals },
] = await Promise.all([productRequest, eventsRequest, signalsRequest])
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
const events = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const signals = computed(() => signalData.value?.items ?? [])
useVisibleRefresh(() => Promise.all([
  productRequest.refresh({ dedupe: 'defer' }),
  eventsRequest.refresh({ dedupe: 'defer' }),
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

    <section class="panel-solid p-4 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <h1 class="flex flex-wrap items-center gap-2 text-lg font-semibold text-slate-900 dark:text-zinc-50">{{ product.name }}<span v-if="communityMeta" class="chip text-xs" :class="communityClasses(communityMeta.color).chip">{{ communityMeta.label }}</span></h1>
          <p class="mt-0.5 text-xs text-slate-600 dark:text-zinc-400">
            {{ product.publisher || '—' }}<template v-if="product.skus.length"> · {{ product.skus.join(' / ') }}</template>
          </p>
        </div>
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ product.fulfillment.consumer.state_label }}</span>
      </div>
      <dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-[13px] sm:grid-cols-4 [&>div]:min-w-0">
        <div v-if="product.price_note">
          <dt class="text-xs text-slate-600 dark:text-zinc-400">价格</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.price_note }}</dd>
        </div>
        <div v-if="product.presale_at">
          <dt class="text-xs text-slate-600 dark:text-zinc-400">预售</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.presale_at }}</dd>
        </div>
        <div v-if="product.balance_window">
          <dt class="text-xs text-slate-600 dark:text-zinc-400">尾款窗口</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.balance_window }}</dd>
        </div>
        <div v-if="product.original_ship_at">
          <dt class="text-xs text-slate-600 dark:text-zinc-400">原定发货</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.original_ship_at }}</dd>
        </div>
      </dl>
      <p v-if="product.ship_note" class="mt-3 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">{{ product.ship_note }}</p>
      <p v-if="product.fulfillment.candidate_total" class="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
        正在自动跟踪 {{ product.fulfillment.candidate_total }} 条信号：当前证据不足，系统会继续采集，暂不计入已采纳结论。
      </p>
      <p class="mt-3 text-xs leading-relaxed text-slate-600 dark:text-zinc-400">持续自动采集与分析，状态随新证据更新。自动采纳与人工确认分别标注，可查看原文依据。</p>
    </section>

    <div class="flex rounded-lg bg-slate-200 p-0.5 text-xs dark:bg-zinc-800" role="tablist" aria-label="商品履约信息">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        class="min-h-11 flex-1 rounded-md px-2.5 py-1 transition-all"
        :aria-selected="view === tab.key"
        :class="view === tab.key ? 'bg-white font-medium text-blue-700 shadow-sm dark:bg-zinc-700 dark:text-blue-300' : 'text-slate-500 hover:text-slate-700 dark:text-zinc-400 dark:hover:text-zinc-200'"
        @click="view = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <section v-if="view === 'overview'" class="space-y-4">
      <div class="grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-3">
        <FulfillmentAxisCard :axis="product.fulfillment.official" tone="blue" />
        <FulfillmentAxisCard :axis="product.fulfillment.consumer" tone="emerald" />
        <FulfillmentAxisCard :axis="product.fulfillment.quality" tone="amber" />
      </div>
      <div v-if="product.fulfillment.sample_count || product.fulfillment.observation_count" class="panel-solid space-y-2 p-4 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <p v-if="product.fulfillment.observation_count">{{ product.fulfillment.observation_count }} 个独立账号展示了实物开箱，购买方式未注明，不计入自购到货样本。</p>
        <p v-if="product.fulfillment.sample_count">另有 {{ product.fulfillment.sample_count }} 个已采纳送测样品来源。送测只作为旁证，不计入普通消费者到货样本。</p>
      </div>
      <section class="panel-solid p-4 sm:p-5">
        <div class="flex items-center justify-between gap-3">
          <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">最近履约信号</h2>
          <button type="button" class="link min-h-11 text-xs" @click="view = 'evidence'">查看全部</button>
        </div>
        <p v-if="signalsError" role="alert" class="mt-3 text-sm text-amber-700 dark:text-amber-300">信号暂时加载失败。<button type="button" class="ml-1 underline" @click="refreshSignals()">重试</button></p>
        <ProductSignalList v-else :items="signals.slice(0, 3)" :community="productCommunity" />
      </section>
    </section>

    <section v-else-if="view === 'timeline'" class="panel-solid overflow-hidden">
      <p v-if="eventsError" role="alert" class="px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">事件暂时加载失败。<button type="button" class="ml-1 underline" @click="refreshEvents()">重试</button></p>
      <EventRow v-for="event in events" :key="event.id" :event="event" />
      <p v-if="!eventsError && !events.length" class="px-4 py-10 text-center text-sm text-slate-400">在已接入来源中暂无该商品的相关事件。</p>
    </section>

    <section v-else class="panel-solid p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">履约信号（{{ signals.length }}）</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-600 dark:text-zinc-400">结论依据已采纳信号，并区分自动采纳、官方来源与人工确认。证据不足的信号继续自动跟踪；实物开箱不等于已确认自购。</p>
      <div class="mt-4">
        <p v-if="signalsError" role="alert" class="text-sm text-amber-700 dark:text-amber-300">信号暂时加载失败。<button type="button" class="ml-1 underline" @click="refreshSignals()">重试</button></p>
        <ProductSignalList v-else :items="signals" :community="productCommunity" />
      </div>
    </section>
  </div>
</template>
