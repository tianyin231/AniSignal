<script setup lang="ts">
import { sourcesHealth } from '~/utils/data'

const shown = sourcesHealth.slice(0, 6)
const failCount = sourcesHealth.filter((s) => s.status === 'fail').length
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
          <span class="size-1.5 shrink-0 rounded-full" :class="s.status === 'ok' ? 'bg-emerald-500' : 'bg-amber-500'" />
          <span class="truncate text-slate-600 dark:text-zinc-300">{{ s.name }}</span>
        </span>
        <span class="mono shrink-0 text-slate-400">{{ s.lastOk }}</span>
      </li>
    </ul>
    <p v-if="failCount" class="mt-2.5 text-xs leading-relaxed text-amber-600 dark:text-amber-400">
      {{ failCount }} 个来源不可用，其范围内「未发现」不代表无消息。
    </p>
  </section>
</template>
