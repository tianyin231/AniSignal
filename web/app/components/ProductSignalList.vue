<script setup lang="ts">
import type { ApiProductSignal } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

defineProps<{ items: ApiProductSignal[]; community?: string }>()

const signalLabels: Record<string, string> = { consumer_received: '实物开箱', quality_issue: '质量反馈' }
</script>

<template>
  <div v-if="items.length" class="divide-y divide-slate-900/5 dark:divide-white/10">
    <article v-for="item in items" :key="item.id" class="min-w-0 py-4 first:pt-0 last:pb-0 [overflow-wrap:anywhere]">
      <div class="flex flex-wrap items-center gap-2">
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ signalLabels[item.signal_type] || item.signal_label }}</span>
        <span class="text-xs text-slate-600 dark:text-zinc-400">{{ item.account || item.platform }} · {{ fmtTime(item.occurred_at) }}</span>
      </div>
      <p class="mt-2 text-sm font-medium text-slate-800 dark:text-zinc-200">{{ item.summary }}</p>
      <blockquote v-if="item.claim_quote && item.claim_quote !== item.summary" class="mt-2 border-l-2 border-slate-200 pl-3 text-xs leading-relaxed text-slate-500 dark:border-zinc-700 dark:text-zinc-400">
        {{ item.claim_quote }}
      </blockquote>
      <div class="mt-3 flex flex-wrap gap-3 text-xs">
        <NuxtLink :to="{ path: `/contents/${item.content_id}`, query: { community: item.community || community } }" class="link min-h-11 inline-flex items-center">查看站内原文</NuxtLink>
        <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link min-h-11 inline-flex items-center">打开来源 ↗</a>
      </div>
    </article>
  </div>
  <p v-else class="py-8 text-center text-sm text-slate-400">暂无履约信号。</p>
</template>
