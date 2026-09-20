<script setup lang="ts">
import type { CalendarDto, CalendarEntry } from '~/composables/usePublicApi'
import { GAME_COMMUNITIES, communityClasses } from '~/composables/useGameSection'
import { fmtTime } from '~/utils/admin-types'

const props = defineProps<{ data: CalendarDto | null | undefined, showCommunity?: boolean }>()
const groups = computed(() => [
  { key: 'ongoing', label: '进行中', items: props.data?.ongoing ?? [] },
  { key: 'upcoming', label: '即将到来', items: props.data?.upcoming ?? [] },
  { key: 'recent', label: '近期开始', items: props.data?.recent ?? [] },
].filter(group => group.items.length))
const kindLabels: Record<string, string> = { activity: '活动', maintenance: '更新 / 维护', pool: '卡池', broadcast: '直播', delay: '变更', deadline: '截止' }
const communityMeta = (key?: string) => GAME_COMMUNITIES.find(c => c.key === key)
function when(item: CalendarEntry, group: string) {
  const date = fmtTime(group === 'ongoing' ? item.end_at : item.start_at)
  const value = item.all_day ? date.slice(0, 10) : date
  if (group === 'recent') return `${value} 已开始 · 结束时间未公布`
  return `${value} ${group === 'ongoing' ? '结束' : item.kind === 'deadline' ? '截止' : '开始'}`
}
</script>

<template>
  <section v-if="groups.length" aria-labelledby="calendar-title" class="mb-6">
    <h2 id="calendar-title" class="mb-3 px-1 text-base font-semibold text-slate-900 dark:text-zinc-50">活动日历</h2>
    <div class="panel divide-y divide-slate-900/5 overflow-hidden dark:divide-white/5">
      <section v-for="group in groups" :key="group.key">
        <h3 class="bg-slate-500/5 px-4 py-2 text-xs font-medium text-slate-600 dark:text-zinc-300">{{ group.label }}</h3>
        <div v-for="item in group.items" :key="item.id" class="flex flex-wrap items-start gap-x-3 gap-y-1 border-t border-slate-900/5 px-4 py-3 dark:border-white/5">
          <div class="min-w-0 flex-1 basis-56">
            <div class="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-zinc-400">
              <span>{{ kindLabels[item.kind] || item.kind }}</span>
              <span v-if="showCommunity && communityMeta(item.community)" class="rounded px-1.5 py-0.5" :class="communityClasses(communityMeta(item.community)!.color).chip">{{ communityMeta(item.community)!.label }}</span>
              <span v-if="item.delayed" class="font-medium text-amber-700 dark:text-amber-300">时间已调整</span>
            </div>
            <NuxtLink :to="`/contents/${item.content_id}`" class="text-sm leading-relaxed text-slate-800 hover:text-blue-700 dark:text-zinc-200 dark:hover:text-blue-300">{{ item.title }}</NuxtLink>
            <p class="mono mt-1 text-xs text-slate-500 dark:text-zinc-400">{{ when(item, group.key) }}</p>
          </div>
          <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link py-1 text-xs">原文</a>
        </div>
      </section>
    </div>
  </section>
</template>
