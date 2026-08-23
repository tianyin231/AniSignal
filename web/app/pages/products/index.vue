<script setup lang="ts">
import type { ApiProductList } from '~/composables/usePublicApi'

useSeoMeta({ title: '商品' })

const { api } = usePublicApi()
const { data, status } = await useAsyncData('products', () =>
  api<ApiProductList>('/api/products'),
)
const products = computed(() => data.value?.items ?? [])
</script>

<template>
  <div>
    <h1 class="mb-4 text-lg font-semibold text-slate-900 dark:text-zinc-50">商品</h1>

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <div v-else-if="products.length" class="grid gap-4 sm:grid-cols-2">
      <NuxtLink
        v-for="p in products"
        :key="p.slug"
        :to="`/products/${p.slug}`"
        class="panel block p-4 transition-shadow hover:shadow-lg sm:p-5"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="truncate font-semibold text-slate-900 dark:text-zinc-50">{{ p.name }}</h2>
            <p class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500">
              {{ p.publisher || '—' }}{{ p.skus.length ? ` · ${p.skus.join(' / ')}` : '' }}
            </p>
          </div>
          <span class="chip shrink-0 bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ p.stage_label }}</span>
        </div>
        <p v-if="p.price_note" class="mono mt-3 text-sm text-slate-600 dark:text-zinc-300">{{ p.price_note }}</p>
        <div class="mt-3">
          <FulfillmentStepper :stages="p.stages" variant="rail" />
        </div>
      </NuxtLink>
    </div>
    <section v-else class="panel px-4 py-10 text-center text-sm text-slate-400">
      暂无追踪中的商品。商品与监控词由管理员在后台维护。
    </section>

    <p class="mt-4 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
      商品履约追踪以公开证据为准：送测样品不等于消费者订单发货；单一账号的开箱需要人工复核。
      第一个商品闭环稳定前不增加新品类。
    </p>
  </div>
</template>
