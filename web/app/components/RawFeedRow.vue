<script setup lang="ts">
import { platformMeta, regionMeta, type RawItem } from '~/utils/data'

defineProps<{ item: RawItem }>()
</script>

<template>
  <div class="flex gap-3 px-4 py-3 transition-colors hover:bg-slate-50 dark:hover:bg-zinc-800/40">
    <span
      class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white"
      :class="platformMeta[item.platform].badge"
    >
      {{ platformMeta[item.platform].short }}
    </span>
    <div class="min-w-0 flex-1">
      <div class="flex items-start justify-between gap-3">
        <p class="min-w-0 truncate text-[14px] text-slate-800 dark:text-zinc-200">{{ item.title }}</p>
        <span class="mono shrink-0 pt-0.5 text-xs text-slate-400">{{ item.publishedAt }}</span>
      </div>
      <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400 dark:text-zinc-500">
        <span>{{ platformMeta[item.platform].label }} · {{ item.account }}</span>
        <span aria-hidden="true">·</span>
        <span>{{ regionMeta[item.region] }}</span>
        <template v-if="item.translated">
          <span aria-hidden="true">·</span>
          <span class="font-medium text-sky-600 dark:text-sky-400">机翻已译</span>
        </template>
        <template v-if="item.mergedInto">
          <span aria-hidden="true">·</span>
          <NuxtLink :to="`/events/${item.mergedInto}`" class="link">已并入事件 ↗</NuxtLink>
        </template>
        <span v-else class="text-slate-300 dark:text-zinc-600">· 独立内容</span>
      </div>
    </div>
  </div>
</template>
