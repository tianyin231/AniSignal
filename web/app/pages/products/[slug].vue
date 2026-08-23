<script setup lang="ts">
import type { ApiEventList, ApiProduct } from '~/composables/usePublicApi'
import { categoryMeta, toAppEvent } from '~/utils/public-utils'
import type { ProductStage } from '~/utils/data'

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()

const { data: productData, error } = await useAsyncData(
  () => `product-${route.params.slug}`,
  () => api<ApiProduct>(`/api/products/${route.params.slug}`),
  { watch: [() => route.params.slug] },
)
if (error.value || !productData.value)
  throw createError({ statusCode: 404, statusMessage: '商品不存在', fatal: true })

const product = computed(() => productData.value!)

const tabs = [
  { key: 'overview', label: '总览' },
  { key: 'timeline', label: '事件时间线' },
  { key: 'evidence', label: '全部证据' },
]
const view = computed({
  get: () => String(route.query.view || 'overview'),
  set: (v: string) => router.replace({ query: { ...route.query, view: v } }),
})

const { data: eventsData } = await useAsyncData(
  () => `product-events-${route.params.slug}`,
  () => api<ApiEventList>('/api/events', { query: { product_slug: route.params.slug, limit: 50 } }),
  { watch: [() => route.params.slug] },
)
const events = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
const allSources = computed(() => events.value.flatMap((e) => e.sources))

const stages = computed<ProductStage[]>(
  () =>
    product.value.stages.map((s) => ({
      label: s.label,
      state: s.state,
      tone: (s.tone as ProductStage['tone']) ?? undefined,
      date: s.date ?? undefined,
      evidence: s.evidence ?? undefined,
      note: s.note ?? undefined,
    })),
)

useSeoMeta({ title: () => `${product.value.name} · 商品` })
</script>

<template>
  <div class="space-y-4">
    <BackButton fallback="/products" label="返回商品" />

    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">{{ product.name }}</h1>
          <p class="mt-0.5 text-xs text-slate-400 dark:text-zinc-500">
            {{ product.publisher || '—' }}<template v-if="product.skus.length"> · {{ product.skus.join(' / ') }}</template>
          </p>
        </div>
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ product.stage_label }}</span>
      </div>
      <dl class="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
        <div v-if="product.price_note">
          <dt class="text-xs text-slate-400">价格</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.price_note }}</dd>
        </div>
        <div v-if="product.presale_at">
          <dt class="text-xs text-slate-400">预售</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.presale_at }}</dd>
        </div>
        <div v-if="product.balance_window">
          <dt class="text-xs text-slate-400">尾款窗口</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.balance_window }}</dd>
        </div>
        <div v-if="product.original_ship_at">
          <dt class="text-xs text-slate-400">原定发货</dt>
          <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ product.original_ship_at }}</dd>
        </div>
      </dl>
      <p v-if="product.ship_note" class="mt-3 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        {{ product.ship_note }}
      </p>
    </section>

    <div class="flex rounded-lg bg-slate-200/60 p-0.5 text-xs dark:bg-zinc-800/70">
      <button
        v-for="t in tabs"
        :key="t.key"
        type="button"
        class="rounded-md px-2.5 py-1 transition-all"
        :class="view === t.key ? 'bg-white font-medium text-blue-700 shadow-sm dark:bg-zinc-700 dark:text-blue-300' : 'text-slate-500 hover:text-slate-700 dark:text-zinc-400 dark:hover:text-zinc-200'"
        @click="view = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <section v-if="view === 'overview'" class="space-y-4">
      <div class="panel p-4 sm:p-5">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">履约阶段</h2>
        <div class="mt-4">
          <FulfillmentStepper :stages="stages" variant="full" />
        </div>
        <p class="mt-4 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
          阶段只依据公开证据推进：送测开箱仅证明样品存在；独立账号数达到阈值才推进"多名用户到货"；
          相近购买时间与普遍到货需要更多样本，暂不自动推进。
        </p>
      </div>
    </section>

    <section v-else-if="view === 'timeline'" class="panel overflow-hidden">
      <NuxtLink v-for="e in events" :key="e.id" :to="`/events/${e.id}`" class="block">
        <EventRow :event="e" />
      </NuxtLink>
      <p v-if="!events.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无该商品的相关事件。
      </p>
    </section>

    <section v-else class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">全部证据（{{ allSources.length }}）</h2>
      <div class="mt-2">
        <SourceTable v-if="allSources.length" :sources="allSources" />
        <p v-else class="py-8 text-center text-sm text-slate-400">暂无证据。</p>
      </div>
    </section>
  </div>
</template>
