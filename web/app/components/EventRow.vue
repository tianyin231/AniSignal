<script setup lang="ts">
import { identityMeta, platformMeta, regionMeta, type AppEvent } from '~/utils/data'

const props = defineProps<{ event: AppEvent }>()

const identity = computed(() => identityMeta[props.event.identity])
const regionsText = computed(() => props.event.regions.map((r) => regionMeta[r]).join(' + '))
const platformsText = computed(() =>
  [...new Set(props.event.sources.map((s) => platformMeta[s.platform].label))].join('/'),
)
</script>

<template>
  <NuxtLink
    :to="`/events/${event.id}`"
    class="group block border-b border-slate-200/70 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-slate-50 dark:border-zinc-800/60 dark:hover:bg-zinc-800/40"
  >
    <div class="flex items-baseline justify-between gap-4">
      <h3
        class="min-w-0 truncate text-[15px] font-medium text-slate-800 transition-colors group-hover:text-blue-700 dark:text-zinc-100 dark:group-hover:text-blue-300"
      >
        {{ event.title }}
      </h3>
      <span class="mono shrink-0 text-xs text-slate-400 dark:text-zinc-500">{{ event.updatedAt }}</span>
    </div>
    <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400 dark:text-zinc-500">
      <span class="font-medium" :class="identity.text">{{ identity.label }}</span>
      <span aria-hidden="true">·</span>
      <span>{{ event.category }}</span>
      <span aria-hidden="true">·</span>
      <span>{{ regionsText }}</span>
      <span v-if="event.regionNote" class="text-amber-600 dark:text-amber-400">{{ event.regionNote }}</span>
      <span aria-hidden="true">·</span>
      <span class="mono">{{ event.evidenceCount }} 证据</span>
      <template v-if="event.sources.length > 1">
        <span aria-hidden="true">·</span>
        <span class="mono">{{ platformsText }} ×{{ event.sources.length }}</span>
      </template>
      <template v-if="event.statusChange">
        <span aria-hidden="true">·</span>
        <span class="font-medium text-blue-600 dark:text-blue-400">变化: {{ event.statusChange }}</span>
      </template>
      <template v-if="event.important">
        <span aria-hidden="true">·</span>
        <span class="font-medium text-rose-600 dark:text-rose-400">重要</span>
      </template>
      <template v-if="event.corrections?.length">
        <span aria-hidden="true">·</span>
        <span class="font-medium text-amber-600 dark:text-amber-400">已更正</span>
      </template>
    </div>
    <p class="mt-1.5 line-clamp-1 text-[13px] leading-relaxed text-slate-500 dark:text-zinc-400">
      {{ event.summary }}
    </p>
  </NuxtLink>
</template>
