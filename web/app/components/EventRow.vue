<script setup lang="ts">
import { identityMeta, platformMeta, type AppEvent } from '~/utils/data'
import { communityClasses, communityMeta } from '~/composables/useGameSection'

const props = defineProps<{ event: AppEvent; showCommunity?: boolean }>()

const identity = computed(() => identityMeta[props.event.identity])
const eventPlatforms = computed(() =>
  [...new Set(props.event.sources.map((s) => s.platform))].map((platform) => ({
    key: platform,
    ...(platformMeta[platform] ?? { label: platform, short: '?', badge: 'bg-slate-500' }),
  })),
)
const multiPlatform = computed(() => eventPlatforms.value.length > 1)
// 聚合视角：事件行标注所属分区
const community = computed(() => {
  if (!props.showCommunity) return null
  const meta = communityMeta(props.event.community)
  return meta ? { label: meta.label, dot: communityClasses(meta.color).dot } : null
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
    class="group block border-b border-line-soft px-4 py-4 transition-colors last:border-b-0 hover:bg-line-soft sm:px-5"
  >
    <div class="flex items-start justify-between gap-4">
      <h3 class="min-w-0 flex-1 text-[15px] font-semibold leading-normal text-ink transition-colors group-hover:text-brand-ink">
        {{ event.title }}
      </h3>
      <span class="mono hidden shrink-0 pt-0.5 text-xs text-faint sm:block" :title="`首次出现 ${event.firstSeenAt}`">{{ event.latestEvidenceAt }}</span>
    </div>
    <div class="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-xs text-muted">
      <!-- 手机端时间并入元信息行，给标题让出整行宽度 -->
      <span class="mono text-faint sm:hidden">{{ event.latestEvidenceAt }}</span>
      <span class="font-semibold" :class="identity.text">{{ identity.label }}</span>
      <span v-if="community" class="inline-flex items-center gap-1.5 text-ink-2">
        <span class="size-[7px] rounded-full" :class="community.dot" aria-hidden="true" />{{ community.label }}
      </span>
      <span class="inline-flex items-center gap-1" aria-label="事件来源平台">
        <span
          v-for="platform in eventPlatforms"
          :key="platform.key"
          class="grid size-[18px] place-items-center rounded-full text-[8px] font-bold text-white"
          :class="platform.badge"
          :title="platform.label"
        >{{ platform.short }}</span>
      </span>
      <span v-if="multiPlatform" class="rounded-md bg-brand-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-ink">跨平台</span>
      <span v-if="sourceNames" class="max-w-[22rem] truncate">{{ sourceNames }}</span>
      <span v-if="event.statusChange" class="font-semibold text-brand-ink">变化：{{ event.statusChange }}</span>
      <span v-if="event.important" class="font-semibold text-rose-600 dark:text-rose-400">重要</span>
      <span v-if="event.corrections?.length" class="font-semibold text-amber-600 dark:text-amber-400">有更新记录</span>
    </div>
  </NuxtLink>
</template>
