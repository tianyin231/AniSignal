<script setup lang="ts">
import { products } from '~/utils/data'

useSeoMeta({ title: '商品' })
</script>

<template>
  <div>
    <h1 class="mb-4 text-lg font-semibold text-slate-900 dark:text-zinc-50">商品</h1>
    <div class="grid gap-4 sm:grid-cols-2">
      <NuxtLink
        v-for="p in products"
        :key="p.slug"
        :to="`/products/${p.slug}`"
        class="panel block p-4 transition-colors hover:border-blue-300 dark:hover:border-blue-500/40"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <h2 class="truncate font-medium text-slate-900 dark:text-zinc-100">{{ p.name }}</h2>
            <p class="mt-0.5 text-xs text-slate-400 dark:text-zinc-500">{{ p.publisher }}</p>
          </div>
          <span class="chip shrink-0 bg-blue-500/10 font-medium text-blue-600 dark:text-blue-300">
            {{ p.stages[p.currentStage].label }}
          </span>
        </div>
        <p class="mono mt-3 text-xs text-slate-400 dark:text-zinc-500">SKU {{ p.skus.join(' / ') }} · {{ p.price }}</p>
      </NuxtLink>
    </div>
    <p class="mt-4 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
      扩张闸门：爱弥斯键盘闭环未稳定前，不增加第二种商品。
    </p>
  </div>
</template>
