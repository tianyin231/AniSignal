<script setup lang="ts">
import type { ApiProductSignal } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

defineProps<{ items: ApiProductSignal[] }>()

const fulfillmentKindLabels: Record<string, string> = {
  self_purchase: '自购开箱',
  pr_sample: '送测样品',
  forward: '转发剪辑',
  none: '普通资讯',
}

const statusMeta = {
  verified: { label: '已核验', cls: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' },
  candidate: { label: '待核验', cls: 'bg-amber-500/10 text-amber-700 dark:text-amber-300' },
  conflict: { label: '证据冲突', cls: 'bg-red-500/10 text-red-700 dark:text-red-300' },
  rejected: { label: '已驳回', cls: 'bg-slate-500/10 text-slate-500' },
} as const
</script>

<template>
  <div v-if="items.length" class="divide-y divide-slate-900/5 dark:divide-white/10">
    <article v-for="item in items" :key="item.id" class="py-4 first:pt-0 last:pb-0">
      <div class="flex flex-wrap items-center gap-2">
        <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ item.signal_label }}</span>
        <span class="chip" :class="statusMeta[item.review_status].cls">{{ statusMeta[item.review_status].label }}</span>
        <span class="text-xs text-slate-400">{{ item.account || item.platform }} · {{ fmtTime(item.occurred_at) }}</span>
      </div>
      <p class="mt-2 text-sm font-medium text-slate-800 dark:text-zinc-200">{{ item.summary }}</p>
      <p
        v-if="item.review_status === 'candidate' && item.ai_hint && item.ai_hint.kind !== 'none'"
        class="mt-2 inline-flex flex-wrap items-center gap-1.5 rounded bg-sky-500/10 px-2 py-1 text-[11px] text-sky-700 dark:text-sky-300"
        :title="item.ai_hint.reason"
      >
        AI 预判：{{ fulfillmentKindLabels[item.ai_hint.kind] ?? item.ai_hint.kind }}
        <span class="mono opacity-70">{{ item.ai_hint.confidence }}%</span> · 仍需人工核验
      </p>
      <blockquote v-if="item.claim_quote" class="mt-2 border-l-2 border-slate-200 pl-3 text-xs leading-relaxed text-slate-500 dark:border-zinc-700 dark:text-zinc-400">
        {{ item.claim_quote }}
      </blockquote>
      <div class="mt-3 flex flex-wrap gap-3 text-xs">
        <NuxtLink :to="`/contents/${item.content_id}`" class="link min-h-11 inline-flex items-center">查看站内原文</NuxtLink>
        <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link min-h-11 inline-flex items-center">打开来源 ↗</a>
      </div>
    </article>
  </div>
  <p v-else class="py-8 text-center text-sm text-slate-400">暂无履约信号。</p>
</template>
