<script setup lang="ts">
import type { ApiEvent, CalendarDto, CodesDto } from '~/composables/usePublicApi'
import { GAME_COMMUNITIES, communityClasses } from '~/composables/useGameSection'
import { fmtTime } from '~/utils/admin-types'
import { utcTimestamp } from '~/utils/public-utils'

const props = defineProps<{ events: ApiEvent[], calendar?: CalendarDto | null, codes?: CodesDto | null, now: number, showCommunity?: boolean }>()
const communityMeta = (key?: string) => GAME_COMMUNITIES.find(c => c.key === key)
type FocusItem = { key: string, title: string, text: string, to: string, label: string, community?: string, source?: string, url?: string | null }
const items = computed<FocusItem[]>(() => {
  const until = props.now + 24 * 60 * 60 * 1000
  const result: FocusItem[] = []
  const usedContents = new Set<number>()
  for (const code of (props.codes?.items ?? []).filter(c => c.expires_at && utcTimestamp(c.expires_at) > props.now && utcTimestamp(c.expires_at) <= until).slice(0, 1)) {
    result.push({ key: `code-${code.community}-${code.code}`, title: `兑换码 ${code.code} 即将到期`, text: `${fmtTime(code.expires_at)} 前兑换`, to: '#codes-title', label: '别错过', community: code.community, source: '官方公告', url: code.url })
  }
  const schedules = [...(props.calendar?.ongoing ?? []), ...(props.calendar?.upcoming ?? [])]
    .map(item => ({ item, ending: utcTimestamp(item.start_at) <= props.now && !!item.end_at, at: utcTimestamp(item.start_at) <= props.now && item.end_at ? item.end_at : item.start_at }))
    .filter(({ at }) => utcTimestamp(at) > props.now && utcTimestamp(at) <= until)
    .sort((a, b) => utcTimestamp(a.at) - utcTimestamp(b.at))
  for (const { item, ending, at } of schedules.slice(0, 2)) {
    usedContents.add(item.content_id)
    const verb = ending ? '结束' : item.kind === 'deadline' ? '截止' : '开始'
    result.push({ key: `schedule-${item.id}`, title: item.title, text: `${item.all_day ? fmtTime(at).slice(0, 10) : fmtTime(at)} ${verb}`, to: `/contents/${item.content_id}`, label: ending || item.kind === 'deadline' ? '即将结束' : '即将开始', community: item.community, source: '官方公告', url: item.url })
  }
  for (const event of props.events) {
    if (result.length >= 5) break
    if (utcTimestamp(event.latest_evidence_at ?? event.first_seen_at) < props.now - 24 * 60 * 60 * 1000) continue
    if (event.sources.some(source => source.content_id != null && usedContents.has(source.content_id))) continue
    event.sources.forEach(source => { if (source.content_id != null) usedContents.add(source.content_id) })
    result.push({ key: `event-${event.id}`, title: event.title, text: event.summary_status === 'fallback' ? '' : (event.summary ?? ''), to: `/events/${event.id}`, label: '新动态', community: event.community, source: event.sources[0]?.account ?? '来源', url: event.sources[0]?.url })
  }
  return result
})
</script>

<template>
  <section aria-labelledby="today-title" class="mb-6">
    <div class="mb-3 flex items-baseline justify-between px-1">
      <h2 id="today-title" class="text-base font-semibold text-slate-900 dark:text-zinc-50">今日要紧的事</h2>
      <span class="text-xs text-slate-500 dark:text-zinc-400">{{ items.length }} 条重点</span>
    </div>
    <div class="panel divide-y divide-slate-900/5 overflow-hidden dark:divide-white/5">
      <article v-for="item in items" :key="item.key" class="px-4 py-3.5">
        <div class="mb-1.5 flex flex-wrap items-center gap-2 text-[11px]">
          <span :class="item.label === '新动态' ? 'text-slate-500 dark:text-zinc-400' : 'font-medium text-amber-700 dark:text-amber-300'">{{ item.label }}</span>
          <span v-if="showCommunity && communityMeta(item.community)" class="rounded px-1.5 py-0.5" :class="communityClasses(communityMeta(item.community)!.color).chip">{{ communityMeta(item.community)!.label }}</span>
        </div>
        <NuxtLink :to="item.to" class="text-sm font-medium leading-relaxed text-slate-900 hover:text-blue-700 dark:text-zinc-100 dark:hover:text-blue-300">{{ item.title }}</NuxtLink>
        <p v-if="item.text" class="mt-1 line-clamp-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-400">{{ item.text }}</p>
        <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link mt-1.5 inline-block text-xs">{{ item.source }} · 原文</a>
      </article>
      <p v-if="!items.length" class="px-4 py-6 text-sm text-slate-500 dark:text-zinc-400">近 24 小时暂无新增重点。</p>
    </div>
  </section>
</template>
