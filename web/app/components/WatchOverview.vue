<script setup lang="ts">
import { watchSlots, deliveries } from '~/utils/data'
</script>

<template>
  <section class="panel p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">我的监控位</h2>
      <span class="text-xs text-slate-400">基础订阅：1 个监控位</span>
    </div>
    <div class="mt-3 space-y-2">
      <div
        v-for="w in watchSlots"
        :key="w.id"
        class="rounded-md border border-slate-200 p-3 dark:border-zinc-800"
      >
        <div class="flex items-center justify-between gap-2">
          <p class="text-sm font-medium text-slate-800 dark:text-zinc-200">{{ w.target }}</p>
          <span class="chip bg-emerald-500/10 font-medium text-emerald-700 dark:text-emerald-300">{{ w.status }}</span>
        </div>
        <p class="mt-1 text-xs text-slate-400 dark:text-zinc-500">
          {{ w.type }} · 购买时间：{{ w.purchaseTime }} · {{ w.channel }}
        </p>
      </div>
    </div>

    <h2 class="mt-6 text-sm font-semibold text-slate-900 dark:text-zinc-100">发送记录</h2>
    <ul class="mt-2 divide-y divide-slate-100 text-sm dark:divide-zinc-800/60">
      <li v-for="d in deliveries" :key="d.at" class="flex flex-wrap items-center justify-between gap-2 py-2.5">
        <span class="flex min-w-0 items-center gap-2">
          <span
            class="chip w-11 shrink-0 justify-center"
            :class="d.type === '即时' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' : 'bg-slate-200/70 text-slate-600 dark:bg-zinc-800 dark:text-zinc-300'"
          >
            {{ d.type }}
          </span>
          <NuxtLink v-if="d.eventId" :to="`/events/${d.eventId}`" class="link truncate">{{ d.event }}</NuxtLink>
          <span v-else class="truncate text-slate-600 dark:text-zinc-300">{{ d.event }}</span>
        </span>
        <span class="mono shrink-0 text-xs text-slate-400">{{ d.at }} · {{ d.status }}</span>
      </li>
    </ul>
    <p class="mt-2.5 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
      每封邮件都注明触发规则与当时的证据快照，可随时退订。
    </p>
  </section>
</template>
