<script setup lang="ts">
import { platformMeta, type Platform } from '~/utils/data'

const props = defineProps<{ platforms: Platform[] }>()
const unique = computed(() => [...new Set(props.platforms)])
</script>

<template>
  <span
    class="inline-flex -space-x-1"
    :title="unique.map((p) => platformMeta[p].label).join(' / ')"
  >
    <span
      v-for="p in unique"
      :key="p"
      class="grid size-[18px] place-items-center rounded-full text-[9px] font-bold text-white ring-2 ring-white dark:ring-zinc-900"
      :class="platformMeta[p].badge"
    >
      {{ platformMeta[p].short }}
    </span>
  </span>
</template>
