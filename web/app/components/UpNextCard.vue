<script setup lang="ts">
import type { CalendarDto, CodesDto } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { beijingParts, relativeDay, upNextItems } from '~/utils/schedule'

// 即将到来：未开始的日程、截止事项与兑换码到期，按时间排前 5 条；倒计时 ≤ 2 天标红
const props = defineProps<{
  calendar: CalendarDto | null | undefined
  codes: CodesDto | null | undefined
  now: number
  calendarScope: string
  compact?: boolean
}>()

const DAY = 86_400_000
const items = computed(() =>
  upNextItems(props.calendar, props.codes?.items, props.now, 5).map((item) => {
    const parts = beijingParts(item.at)
    const meta = communityMeta(item.community)
    return {
      ...item,
      day: parts.day,
      weekday: parts.weekday,
      time: item.allDay ? '全天' : parts.hm,
      game: meta?.label,
      dot: meta ? communityClasses(meta.color).dot : '',
      relative: relativeDay(item.at, props.now),
      urgent: item.at - props.now <= 2 * DAY,
    }
  }),
)
</script>

<template>
  <section class="panel px-5 pb-2 pt-4" :class="compact ? 'xl:flex xl:min-h-0 xl:flex-col xl:p-3' : ''" aria-labelledby="up-next-title">
    <header class="flex shrink-0 items-center justify-between">
      <h2 id="up-next-title" class="text-base font-bold text-ink">即将到来</h2>
      <NuxtLink :to="{ path: '/calendar', query: { community: calendarScope } }" class="link text-[13px]">月历</NuxtLink>
    </header>
    <ol v-if="items.length" class="mt-2" :class="compact ? 'xl:grid xl:min-h-0 xl:flex-1 xl:auto-rows-fr' : ''">
      <li v-for="item in items" :key="item.key" class="grid grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-3 border-t border-line-soft py-2.5" :class="compact ? 'xl:grid-cols-[24px_minmax(0,1fr)_auto] xl:gap-2 xl:py-0.5' : ''">
        <div class="text-center leading-tight">
          <div class="num text-lg font-bold text-ink" :class="compact ? 'xl:text-[13px] xl:leading-5' : ''">{{ item.day }}</div>
          <div class="text-[11px] text-muted" :class="compact ? 'xl:hidden' : ''">{{ item.weekday }}</div>
        </div>
        <div class="min-w-0">
          <NuxtLink v-if="item.contentId" :to="`/contents/${item.contentId}`" :title="[item.game, item.kind, item.time].filter(Boolean).join(' · ')" class="block truncate text-sm font-semibold text-ink hover:text-brand-ink">{{ item.title }}</NuxtLink>
          <a v-else-if="item.url" :href="item.url" :title="[item.game, item.kind, item.time].filter(Boolean).join(' · ')" target="_blank" rel="noopener noreferrer" class="block truncate text-sm font-semibold text-ink hover:text-brand-ink">{{ item.title }}</a>
          <p v-else class="truncate text-sm font-semibold text-ink">{{ item.title }}</p>
          <p class="mt-0.5 flex items-center gap-1.5 text-xs text-muted" :class="compact ? 'xl:hidden' : ''">
            <span v-if="item.game" class="size-[7px] shrink-0 rounded-full" :class="item.dot" aria-hidden="true" />
            <span class="truncate">{{ item.game ? `${item.game} · ` : '' }}{{ item.kind }}</span>
            <span class="mono shrink-0 text-faint">{{ item.time }}</span>
          </p>
        </div>
        <span
          class="inline-flex h-6 shrink-0 items-center rounded-full px-2.5 text-xs font-semibold"
          :class="[item.urgent ? 'bg-hot/12 text-hot' : 'bg-chip text-ink-2', compact ? 'xl:h-5 xl:px-2 xl:text-[11px]' : '']"
        >{{ item.relative }}</span>
      </li>
    </ol>
    <p v-else class="border-t border-line-soft py-6 text-center text-sm text-muted" :class="compact ? 'xl:grid xl:flex-1 xl:place-items-center' : ''">近期没有待开始的日程。</p>
  </section>
</template>
