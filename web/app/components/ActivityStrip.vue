<script setup lang="ts">
import type { CalendarEntry } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { fmtRemaining, fmtShort, KIND_LABELS, ongoingActivities } from '~/utils/schedule'

/**
 * 进行中活动：一张卡片一条，进度条显示已过去的时间比例，剩余时间不足 4 天标红。
 * 默认最多两行（10 条），多出的折叠到"展开全部"。
 */
const props = defineProps<{
  entries: CalendarEntry[]
  now: number
  calendarScope: string
  showCommunity?: boolean
}>()

const LIMIT = 10
const expanded = ref(false)
const all = computed(() =>
  ongoingActivities(props.entries, props.now).map((item) => {
    const meta = communityMeta(item.entry.community)
    return {
      ...item,
      game: props.showCommunity ? meta?.label : undefined,
      chip: meta ? communityClasses(meta.color).chip : '',
      bar: meta ? communityClasses(meta.color).dot : 'bg-accent',
      kind: KIND_LABELS[item.entry.kind] ?? '活动',
      remaining: fmtRemaining(item.remainingMs),
      end: fmtShort(item.endTs),
    }
  }),
)
const shown = computed(() => (expanded.value ? all.value : all.value.slice(0, LIMIT)))
</script>

<template>
  <section v-if="all.length" aria-labelledby="ongoing-title" class="mt-10">
    <header class="wall-heading mb-3.5 flex items-baseline justify-between gap-4">
      <h2 id="ongoing-title" class="text-xl font-bold text-ink">
        进行中<span class="num ml-2 text-base font-medium text-faint">{{ all.length }}</span>
      </h2>
      <NuxtLink :to="{ path: '/calendar', query: { community: calendarScope } }" class="link text-[13px]">活动日历</NuxtLink>
    </header>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <NuxtLink
        v-for="item in shown"
        :key="item.entry.id"
        :to="`/contents/${item.entry.content_id}`"
        class="panel group flex min-h-[132px] flex-col gap-2.5 rounded-[14px] p-3.5 transition-colors hover:border-faint/50 sm:px-4"
      >
        <div class="flex items-center justify-between gap-2 text-xs">
          <span v-if="item.game" class="truncate rounded-md px-1.5 py-0.5 font-semibold" :class="item.chip">{{ item.game }}</span>
          <span class="shrink-0 text-muted">{{ item.kind }}</span>
        </div>
        <p class="line-clamp-2 flex-1 text-sm font-semibold leading-snug text-ink group-hover:text-brand-ink">{{ item.entry.title }}</p>
        <div>
          <div class="h-1 overflow-hidden rounded-full bg-line-soft" role="progressbar" :aria-valuenow="item.percent" aria-valuemin="0" aria-valuemax="100" :aria-label="`${item.entry.title} 已进行 ${item.percent}%`">
            <div class="h-full rounded-full" :class="item.bar" :style="{ width: `${item.percent}%` }" />
          </div>
          <div class="mt-1.5 flex items-baseline justify-between gap-2 text-xs">
            <span :class="item.endingSoon ? 'font-bold text-hot' : 'font-semibold text-ink-2'">剩 {{ item.remaining }}</span>
            <span class="mono hidden truncate text-[11px] text-faint sm:inline">{{ item.end }}</span>
          </div>
        </div>
      </NuxtLink>
    </div>
    <button
      v-if="all.length > LIMIT"
      type="button"
      class="mt-3 h-10 w-full rounded-[10px] border border-line text-[13px] text-ink-2 transition-colors hover:bg-chip"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : `展开全部 ${all.length} 个活动` }}
    </button>
  </section>
</template>
