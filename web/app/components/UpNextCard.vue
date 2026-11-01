<script setup lang="ts">
import type { CalendarDto, CodesDto } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { beijingParts, relativeDay, upNextItems } from '~/utils/schedule'

// 即将到来：未开始的日程、截止事项与兑换码到期，按时间排前 limit 条；倒计时 ≤ 2 天标红。
// footer：卡片被拉伸到与精选大图等高时，用底栏收尾（总数 + 月历入口），不留一截空白。
//   always = 总是显示；auto = 仅当卡片独占所在栏（同栏的履约卡因无商品未渲染）时显示。
const props = withDefaults(defineProps<{
  calendar: CalendarDto | null | undefined
  codes: CodesDto | null | undefined
  now: number
  calendarScope: string
  limit?: number
  footer?: 'always' | 'auto'
}>(), { limit: 5, footer: undefined })

const DAY = 86_400_000
const total = computed(() => upNextItems(props.calendar, props.codes?.items, props.now, 99).length)
const calendarTo = computed(() => ({ path: '/calendar', query: { community: props.calendarScope } }))
const items = computed(() =>
  upNextItems(props.calendar, props.codes?.items, props.now, props.limit).map((item) => {
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
  <section class="up-next panel flex flex-col px-5 pb-2 pt-4" :data-footer="footer" aria-labelledby="up-next-title">
    <header class="flex items-center justify-between">
      <h2 id="up-next-title" class="text-base font-bold text-ink">即将到来</h2>
      <NuxtLink :to="calendarTo" class="link text-[13px]">月历</NuxtLink>
    </header>
    <ol v-if="items.length" class="mt-2 flex-1">
      <li v-for="item in items" :key="item.key" class="grid grid-cols-[40px_minmax(0,1fr)_auto] items-center gap-3 border-t border-line-soft py-2.5">
        <div class="text-center leading-tight">
          <div class="num text-lg font-bold text-ink">{{ item.day }}</div>
          <div class="text-[11px] text-muted">{{ item.weekday }}</div>
        </div>
        <div class="min-w-0">
          <NuxtLink v-if="item.contentId" :to="`/contents/${item.contentId}`" class="block truncate text-sm font-semibold text-ink hover:text-brand-ink">{{ item.title }}</NuxtLink>
          <a v-else-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="block truncate text-sm font-semibold text-ink hover:text-brand-ink">{{ item.title }}</a>
          <p v-else class="truncate text-sm font-semibold text-ink">{{ item.title }}</p>
          <p class="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
            <span v-if="item.game" class="size-[7px] shrink-0 rounded-full" :class="item.dot" aria-hidden="true" />
            <span class="truncate">{{ item.game ? `${item.game} · ` : '' }}{{ item.kind }}</span>
            <span class="mono shrink-0 text-faint">{{ item.time }}</span>
          </p>
        </div>
        <span
          class="inline-flex h-6 shrink-0 items-center rounded-full px-2.5 text-xs font-semibold"
          :class="item.urgent ? 'bg-hot/12 text-hot' : 'bg-chip text-ink-2'"
        >{{ item.relative }}</span>
      </li>
    </ol>
    <p v-else class="flex-1 border-t border-line-soft py-6 text-center text-sm text-muted">近期没有待开始的日程。</p>
    <footer v-if="footer && items.length" class="up-next-footer items-center justify-between gap-2 border-t border-line-soft py-2.5 text-xs text-muted">
      <span>共 <span class="num text-[13px] font-semibold text-ink-2">{{ total }}</span> 项待开始</span>
      <NuxtLink :to="calendarTo" class="link text-[13px]">查看月历</NuxtLink>
    </footer>
  </section>
</template>

<style scoped>
.up-next-footer { display: none; }
.up-next[data-footer='always'] .up-next-footer,
.up-next[data-footer='auto']:only-child .up-next-footer { display: flex; }
</style>
