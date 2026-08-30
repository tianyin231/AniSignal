<script setup lang="ts">
import type { FulfillmentAxis } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const props = defineProps<{
  axis: FulfillmentAxis
  tone?: 'blue' | 'emerald' | 'amber'
}>()

const toneClass = computed(() => ({
  blue: 'border-blue-500/20 bg-blue-500/[0.04] text-blue-700 dark:text-blue-300',
  emerald: 'border-emerald-500/20 bg-emerald-500/[0.04] text-emerald-700 dark:text-emerald-300',
  amber: 'border-amber-500/20 bg-amber-500/[0.04] text-amber-700 dark:text-amber-300',
}[props.tone || 'blue']))
</script>

<template>
  <article class="rounded-xl border p-4" :class="toneClass">
    <div class="flex items-start justify-between gap-3">
      <p class="text-xs font-medium opacity-80">{{ axis.label }}</p>
      <span v-if="axis.candidate_count" class="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
        {{ axis.candidate_count }} 条待核验
      </span>
    </div>
    <p class="mt-2 text-sm font-semibold text-slate-900 dark:text-zinc-100">{{ axis.state_label }}</p>
    <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ axis.summary }}</p>
    <div class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
      <span>{{ axis.verified_count }} 条已核验信号</span>
      <span v-if="axis.updated_at" class="mono">{{ fmtTime(axis.updated_at) }}</span>
    </div>
  </article>
</template>
