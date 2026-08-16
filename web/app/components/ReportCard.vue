<script setup lang="ts">
import { morningReport } from '~/utils/data'

const headlines = morningReport.sections[0].items
</script>

<template>
  <section class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">今日早报</h2>
      <NuxtLink to="/?tab=report" class="link text-xs">查看全文</NuxtLink>
    </div>
    <p class="mono mt-1 text-xs text-slate-400 dark:text-zinc-500">{{ morningReport.date }} {{ morningReport.sentAt }} 已发送</p>
    <ul class="mt-2.5 space-y-1.5">
      <li
        v-for="it in headlines"
        :key="it.text"
        class="flex gap-2 text-[13px] leading-snug text-slate-600 dark:text-zinc-300"
      >
        <span class="mt-1.5 size-1 shrink-0 rounded-full bg-blue-400" />
        <NuxtLink v-if="it.eventId" :to="`/events/${it.eventId}`" class="transition-colors hover:text-blue-600 dark:hover:text-blue-300">
          {{ it.text }}
        </NuxtLink>
        <span v-else>{{ it.text }}</span>
      </li>
    </ul>
  </section>
</template>
