<script setup lang="ts">
import type { PublicStats } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/public-utils'

// 今日速览：真实采集/事件数据（替代"早报规划中"占位卡）
const { api } = usePublicApi()
const { data: stats } = await useAsyncData('rail-stats', () => api<PublicStats>('/api/stats'))
</script>

<template>
  <section class="panel-glass p-4">
    <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">今日速览</h2>
    <div v-if="stats" class="mt-3 space-y-2">
      <div class="flex items-baseline justify-between gap-2 text-xs">
        <span class="text-slate-500 dark:text-zinc-400">内容库</span>
        <span class="mono text-sm font-semibold text-slate-700 dark:text-zinc-200">{{ stats.total_contents.toLocaleString() }} 条</span>
      </div>
      <div class="flex items-baseline justify-between gap-2 text-xs">
        <span class="text-slate-500 dark:text-zinc-400">官方来源</span>
        <span class="mono text-sm font-semibold text-slate-700 dark:text-zinc-200">{{ stats.official_contents.toLocaleString() }} 条</span>
      </div>
      <p class="mono border-t border-slate-100 pt-2 text-[11px] text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        最近采集 {{ fmtTime(stats.latest_collected_at) }}
      </p>
    </div>
  </section>
</template>
