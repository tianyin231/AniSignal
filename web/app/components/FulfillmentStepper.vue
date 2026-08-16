<script setup lang="ts">
import type { ProductStage } from '~/utils/data'

const props = defineProps<{ stages: ProductStage[]; variant?: 'rail' | 'full' }>()

const dotClass = (s: ProductStage) => {
  if (s.state === 'current') return 'bg-blue-600 stage-pulse'
  if (s.state === 'pending') return 'border-2 border-slate-300 bg-white dark:border-zinc-700 dark:bg-zinc-900'
  if (s.tone === 'amber') return 'bg-amber-500'
  if (s.tone === 'emerald') return 'bg-emerald-500'
  if (s.tone === 'sky') return 'bg-sky-500'
  return 'bg-slate-400 dark:bg-zinc-600'
}
const lineClass = (s: ProductStage) =>
  s.state === 'done' ? 'bg-slate-300 dark:bg-zinc-700' : 'bg-slate-200 dark:bg-zinc-800'
const labelClass = (s: ProductStage) =>
  s.state === 'current'
    ? 'font-medium text-slate-900 dark:text-zinc-50'
    : s.state === 'done'
      ? 'text-slate-600 dark:text-zinc-300'
      : 'text-slate-400 dark:text-zinc-500'
</script>

<template>
  <ol>
    <li v-for="(s, i) in stages" :key="s.label" class="flex gap-3" :class="i < stages.length - 1 ? 'pb-4' : ''">
      <div class="flex flex-col items-center">
        <span class="mt-1 size-2.5 shrink-0 rounded-full" :class="dotClass(s)" />
        <span v-if="i < stages.length - 1" class="w-px flex-1" :class="lineClass(s)" />
      </div>
      <div class="min-w-0 flex-1 pb-0.5" :class="variant === 'full' ? '' : '-mt-1.5'">
        <div class="flex flex-wrap items-baseline gap-x-2">
          <span class="text-sm" :class="labelClass(s)">{{ s.label }}</span>
          <span v-if="s.date" class="mono text-xs text-slate-400">{{ s.date }}</span>
          <span v-if="s.evidence" class="mono text-xs text-slate-400">{{ s.evidence }} 证据</span>
          <span v-if="s.state === 'current'" class="chip bg-blue-500/10 font-medium text-blue-600 dark:text-blue-300">当前阶段</span>
        </div>
        <p
          v-if="variant === 'full' && s.note"
          class="mt-0.5 text-xs leading-relaxed"
          :class="s.tone === 'amber' ? 'text-amber-700 dark:text-amber-300' : 'text-slate-500 dark:text-zinc-400'"
        >
          {{ s.note }}
        </p>
      </div>
    </li>
  </ol>
</template>
