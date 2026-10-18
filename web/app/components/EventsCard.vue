<script setup lang="ts">
import type { ApiEvent } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { platformMeta, type Platform } from '~/utils/data'
import { fmtShort, toTimestamp } from '~/utils/schedule'
import { decodeEntities } from '~/utils/text'

// 聚合事件速览：多来源归并后的结论，按最新证据时间排序；详情页可看证据链
const props = defineProps<{ events: ApiEvent[], showCommunity?: boolean, moreTo: Record<string, unknown> }>()

const rows = computed(() =>
  props.events.slice(0, 5).map((event) => {
    const meta = props.showCommunity ? communityMeta(event.community) : undefined
    const platforms = [...new Set(event.sources.map(s => platformMeta[s.platform as Platform]?.label ?? s.platform))]
    const at = toTimestamp(event.latest_evidence_at ?? event.first_seen_at)
    return {
      id: event.id,
      title: decodeEntities(event.title),
      sources: event.sources.length,
      platforms: platforms.join(' / '),
      game: meta?.label,
      dot: meta ? communityClasses(meta.color).dot : '',
      time: Number.isFinite(at) ? fmtShort(at) : '',
    }
  }),
)
</script>

<template>
  <section v-if="rows.length" class="panel px-5 pb-2 pt-4" aria-labelledby="events-card-title">
    <header class="flex items-center justify-between">
      <h2 id="events-card-title" class="text-base font-bold text-ink">聚合事件</h2>
      <NuxtLink :to="moreTo" class="link text-[13px]">全部事件</NuxtLink>
    </header>
    <ul class="mt-2">
      <li v-for="row in rows" :key="row.id" class="border-t border-line-soft">
        <NuxtLink :to="`/events/${row.id}`" class="group block py-2.5">
          <span class="line-clamp-2 text-sm font-semibold leading-snug text-ink group-hover:text-brand-ink">{{ row.title }}</span>
          <span class="mt-1 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
            <span v-if="row.game" class="size-[7px] rounded-full" :class="row.dot" aria-hidden="true" />
            <span v-if="row.game">{{ row.game }} ·</span>
            <span><span class="num font-semibold text-ink-2">{{ row.sources }}</span> 个来源 · {{ row.platforms }}</span>
            <span class="mono text-faint">{{ row.time }}</span>
          </span>
        </NuxtLink>
      </li>
    </ul>
  </section>
</template>
