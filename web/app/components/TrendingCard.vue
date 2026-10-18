<script setup lang="ts">
import { Flame } from 'lucide-vue-next'
import type { RawItem } from '~/utils/data'
import { communityClasses, communityMeta } from '~/composables/useGameSection'

// 24 小时飙升：窗口内播放增量最高的内容（快照增量，非绝对播放量）
const props = defineProps<{ items: RawItem[], deltas: Map<string, number>, moreTo: Record<string, unknown> }>()

function fmtDelta(n: number) {
  if (n <= 0) return ''
  return n >= 10_000 ? `+${(n / 10_000).toFixed(1)}万` : `+${n.toLocaleString('zh-CN')}`
}
const rows = computed(() =>
  props.items.map((item, index) => {
    const meta = communityMeta(item.community)
    return {
      item,
      rank: index + 1,
      delta: fmtDelta(props.deltas.get(item.id) ?? 0),
      game: meta?.label,
      dot: meta ? communityClasses(meta.color).dot : '',
    }
  }),
)
const failed = ref<string[]>([])
</script>

<template>
  <section v-if="rows.length" class="panel px-5 pb-2 pt-4" aria-labelledby="trending-title">
    <header class="flex items-center justify-between">
      <h2 id="trending-title" class="flex items-center gap-1.5 text-base font-bold text-ink">
        <Flame :size="17" class="text-hot" aria-hidden="true" />24 小时飙升
      </h2>
      <NuxtLink :to="moreTo" class="link text-[13px]">更多</NuxtLink>
    </header>
    <ol class="mt-2">
      <li v-for="row in rows" :key="row.item.id" class="border-t border-line-soft">
        <NuxtLink :to="`/contents/${row.item.id}`" class="group grid grid-cols-[20px_64px_minmax(0,1fr)] items-center gap-3 py-2.5">
          <span class="num text-center text-lg font-bold" :class="row.rank <= 3 ? 'text-ink' : 'text-faint'">{{ row.rank }}</span>
          <img
            v-if="row.item.cover && !failed.includes(row.item.id)"
            :src="row.item.cover"
            alt=""
            referrerpolicy="no-referrer"
            loading="lazy"
            class="aspect-video w-16 rounded-md object-cover"
            @error="failed.push(row.item.id)"
          >
          <span v-else class="aspect-video w-16 rounded-md bg-chip" aria-hidden="true" />
          <span class="min-w-0">
            <span class="line-clamp-2 text-[13px] font-semibold leading-snug text-ink group-hover:text-brand-ink">{{ row.item.title }}</span>
            <span class="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
              <span v-if="row.delta" class="num font-bold text-hot">{{ row.delta }}</span>
              <span v-if="row.game" class="size-[7px] shrink-0 rounded-full" :class="row.dot" aria-hidden="true" />
              <span v-if="row.game" class="truncate">{{ row.game }}</span>
            </span>
          </span>
        </NuxtLink>
      </li>
    </ol>
  </section>
</template>
