<script setup lang="ts">
import { identityMeta, platformMeta, regionMeta, type AppEvent } from '~/utils/data'

const props = defineProps<{ event: AppEvent }>()

const identity = computed(() => identityMeta[props.event.identity])
const regionsText = computed(() => props.event.regions.map((r) => regionMeta[r]).join(' + '))
const eventPlatforms = computed(() =>
  [...new Set(props.event.sources.map((s) => s.platform))].map((platform) => ({
    key: platform,
    ...platformMeta[platform],
  })),
)
const multiPlatform = computed(
  () => new Set(props.event.sources.map((s) => s.platform)).size > 1,
)
</script>

<template>
  <NuxtLink
    :to="`/events/${event.id}`"
    class="group block border-b border-slate-200/70 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-slate-50 dark:border-zinc-800/60 dark:hover:bg-zinc-800/40"
  >
    <div class="flex items-start justify-between gap-4">
      <div class="min-w-0 flex-1">
        <h3
          class="truncate text-[15px] font-medium text-slate-800 transition-colors group-hover:text-blue-700 dark:text-zinc-100 dark:group-hover:text-blue-300"
        >
          {{ event.title }}
        </h3>
        <div class="mt-1.5 flex flex-wrap items-center gap-1.5" aria-label="事件来源平台">
          <span
            v-for="platform in eventPlatforms"
            :key="platform.key"
            class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium"
            :class="platform.key === 'youtube'
              ? 'bg-red-500/10 text-red-700 dark:text-red-300'
              : 'bg-slate-500/10 text-slate-600 dark:text-zinc-300'"
          >
            <span class="grid size-3.5 place-items-center rounded-full text-[7px] font-bold text-white" :class="platform.badge">
              {{ platform.short }}
            </span>
            {{ platform.label }}
          </span>
          <span v-if="multiPlatform" class="rounded-full bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:text-blue-300">
            跨平台
          </span>
        </div>
      </div>
      <span class="mono shrink-0 text-xs text-slate-400 dark:text-zinc-500" :title="`最近更新 ${event.updatedAt}`">{{ event.firstSeenAt }}</span>
    </div>
    <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400 dark:text-zinc-500">
      <span class="font-medium" :class="identity.text">{{ identity.label }}</span>
      <span aria-hidden="true">·</span>
      <span>{{ event.category }}</span>
      <template v-if="event.regions.length > 1">
        <span aria-hidden="true">·</span>
        <span>{{ regionsText }}</span>
      </template>
      <span v-if="event.regionNote" class="text-amber-600 dark:text-amber-400">{{ event.regionNote }}</span>
      <span aria-hidden="true">·</span>
      <span class="mono">{{ event.evidenceCount }} 条证据</span>
      <span aria-hidden="true">·</span>
      <span>可信度 {{ event.confidence }}</span>
      <template v-if="event.matchedKeywords?.length">
        <span aria-hidden="true">·</span>
        <span class="inline-flex flex-wrap gap-1">
          <span v-for="k in event.matchedKeywords" :key="k" class="rounded bg-blue-500/10 px-1.5 py-0.5 text-[11px] text-blue-700 dark:text-blue-300">{{ k }}</span>
        </span>
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
    <p class="mt-1.5 line-clamp-2 text-[13px] leading-relaxed text-slate-500 dark:text-zinc-400">
      {{ event.summary }}
    </p>
  </NuxtLink>
</template>
