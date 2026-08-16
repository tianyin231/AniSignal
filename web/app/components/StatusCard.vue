<script setup lang="ts">
import { product } from '~/utils/data'
</script>

<template>
  <section class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">商品状态</h2>
      <NuxtLink :to="`/products/${product.slug}`" class="link text-xs">详情</NuxtLink>
    </div>
    <p class="mt-1 truncate text-xs text-slate-400 dark:text-zinc-500">{{ product.name }}</p>
    <div class="mt-3">
      <FulfillmentStepper :stages="product.stages" variant="rail" />
    </div>
    <p class="mt-2 border-t border-slate-100 pt-2.5 text-xs leading-relaxed text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
      更新于 <span class="mono">08-16 06:40</span> · 送测样品不等于消费者订单发货
    </p>
  </section>
</template>
