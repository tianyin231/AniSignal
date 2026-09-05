<script setup lang="ts">
import { identityMeta, platformMeta, type AppEvent } from '~/utils/data'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'

const props = defineProps<{ event: AppEvent; showCommunity?: boolean }>()

const identity = computed(() => identityMeta[props.event.identity])
const eventPlatforms = computed(() =>
  [...new Set(props.event.sources.map((s) => s.platform))].map((platform) => ({
    key: platform,
    ...platformMeta[platform],
  })),
)
const multiPlatform = computed(
  () => new Set(props.event.sources.map((s) => s.platform)).size > 1,
)
// 聚合首页：事件行标注所属分区
const communityBadge = computed(() => {
  if (!props.showCommunity || !props.event.community) return null
  const meta = GAME_COMMUNITIES.find(c => c.key === props.event.community)
  return meta ? { label: meta.label, chip: communityClasses(meta.color).chip } : null
})
// 来源账号去重；过多时折叠计数，详情页可见全部
const sourceNames = computed(() => {
  const names = [...new Set(props.event.sources.map((s) => s.account).filter(Boolean))]
  if (names.length === 0) return ''
  if (names.length <= 2) return names.join('、')
  return `${names.slice(0, 2).join('、')} 等 ${names.length} 个来源`
})
</script>

<template>
  <NuxtLink
    :to="`/events/${event.id}`"
    class="group block border-b border-slate-200/70 px-4 py-3.5 transition-colors last:border-b-0 hover:bg-slate-900/[0.04] dark:border-zinc-800/60 dark:hover:bg-zinc-800/40"
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
            v-if="communityBadge"
            class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
            :class="communityBadge.chip"
          >{{ communityBadge.label }}</span>
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
      <span class="mono shrink-0 text-xs text-slate-400 dark:text-zinc-500" :title="`首次出现 ${event.firstSeenAt}`">{{ event.latestEvidenceAt }}</span>
    </div>
    <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400 dark:text-zinc-500">
      <span class="font-medium" :class="identity.text">{{ identity.label }}</span>
      <template v-if="sourceNames">
        <span aria-hidden="true">·</span>
        <span class="truncate">{{ sourceNames }}</span>
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
  </NuxtLink>
</template>
