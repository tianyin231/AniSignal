<script setup lang="ts">
import type { PublicSource } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const { api } = usePublicApi()
const { community } = useGameSection()
const { data } = await useAsyncData(
  () => `rail-sources-${community.value}`,
  () => api<{ items: PublicSource[] }>('/api/sources', { query: { community: community.value } }),
  { watch: [community] },
)
const shown = computed(() =>
  (data.value?.items ?? [])
    .filter((s) => s.enabled)
    .slice(0, 6)
    .map((s) => ({ ...s, ok: s.consecutive_failures === 0 })),
)
</script>

<template>
  <section class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">来源健康</h2>
      <NuxtLink to="/sources" class="link text-xs">全部来源</NuxtLink>
    </div>
    <ul class="mt-2.5 space-y-1.5">
      <li v-for="s in shown" :key="s.name" class="flex items-center justify-between gap-2 text-xs">
        <span class="flex min-w-0 items-center gap-1.5">
          <span class="size-1.5 shrink-0 rounded-full" :class="s.ok ? 'bg-emerald-500' : 'bg-amber-500'" />
          <span class="truncate text-slate-600 dark:text-zinc-300">{{ s.name }}</span>
        </span>
        <span class="mono shrink-0 text-slate-400">{{ fmtTime(s.last_success_at) }}</span>
      </li>
    </ul>
  </section>
</template>
