<script setup lang="ts">
import type { FulfillmentAxis } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const props = defineProps<{
  axis: FulfillmentAxis
  tone?: 'blue' | 'emerald' | 'amber'
}>()

const toneClass = computed(() => ({
  blue: 'border-blue-200 text-blue-700 dark:border-blue-900 dark:text-blue-300',
  emerald: 'border-emerald-200 text-emerald-700 dark:border-emerald-900 dark:text-emerald-300',
  amber: 'border-amber-200 text-amber-800 dark:border-amber-900 dark:text-amber-300',
}[props.tone || 'blue']))
</script>

<template>
  <article class="panel min-w-0 p-4 [overflow-wrap:anywhere]" :class="toneClass">
    <div class="flex flex-wrap items-start justify-between gap-2">
      <p class="text-xs font-medium">{{ axis.label }}</p>
      <span v-if="axis.candidate_count" class="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800 dark:bg-amber-950 dark:text-amber-200">
        {{ axis.candidate_count }} 条自动跟踪
      </span>
    </div>
    <p class="mt-2 text-sm font-semibold text-slate-900 dark:text-zinc-100">{{ axis.state_label }}</p>
    <p class="mt-1 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">{{ axis.summary }}</p>
    <div class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600 dark:text-zinc-400">
      <span>{{ axis.verified_count }} 条已采纳{{ axis.key === 'consumer' ? '自购' : '' }}信号</span>
      <span v-if="axis.automated_count || axis.manual_count">自动 {{ axis.automated_count || 0 }} · 人工 {{ axis.manual_count || 0 }}</span>
      <span v-if="axis.updated_at" class="mono">{{ fmtTime(axis.updated_at) }}</span>
    </div>
  </article>
</template>
