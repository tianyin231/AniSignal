<script setup lang="ts">
import { regionMeta, type Region } from '~/utils/data'

const props = defineProps<{ regions: Region[]; note?: string }>()
const all: Region[] = ['cn', 'global']
</script>

<template>
  <span class="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
    <span
      v-for="r in all"
      :key="r"
      class="inline-flex items-center gap-0.5"
      :class="props.regions.includes(r) ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-400 dark:text-zinc-500'"
    >
      <span class="mono">{{ props.regions.includes(r) ? '✓' : '—' }}</span>{{ regionMeta[r] }}
    </span>
    <span v-if="note" class="text-amber-600 dark:text-amber-400">{{ note }}</span>
  </span>
</template>
