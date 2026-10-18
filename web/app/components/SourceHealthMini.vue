<script setup lang="ts">
import type { PublicSource } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const { api } = usePublicApi()
const { community } = useGameSection()
const { data } = await useLazyAsyncData(
  () => `rail-sources-${community.value}`,
  (_app, { signal }) => api<{ items: PublicSource[] }>('/api/sources', { query: { community: community.value }, signal }),
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
  <section class="panel p-5">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-base font-bold text-ink">来源健康</h2>
      <NuxtLink to="/sources" class="link text-[13px]">全部来源</NuxtLink>
    </div>
    <ul class="mt-3 space-y-2">
      <li v-for="s in shown" :key="s.name" class="flex items-center justify-between gap-2 text-xs">
        <span class="flex min-w-0 items-center gap-2">
          <span class="size-1.5 shrink-0 rounded-full" :class="s.ok ? 'bg-ok' : 'bg-warn'" />
          <span class="truncate text-ink-2">{{ s.name }}</span>
        </span>
        <span class="mono shrink-0 text-faint">{{ fmtTime(s.last_success_at) }}</span>
      </li>
    </ul>
  </section>
</template>
