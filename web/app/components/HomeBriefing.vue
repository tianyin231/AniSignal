<script setup lang="ts">
import { beijingParts } from '~/utils/schedule'

// 聚合首页页头：日期 + "今日信号" + 三个核心数字（24h 新内容 / 进行中活动 / 来源健康）
const props = defineProps<{
  now: number
  newCount: number | null
  ongoing: number
  sourcesOk: number
  sourcesTotal: number
}>()

const dateLine = computed(() => {
  const p = beijingParts(props.now)
  return `${p.year}.${String(p.month).padStart(2, '0')}.${String(p.day).padStart(2, '0')} ${p.weekday} · ${p.hm} 更新`
})
</script>

<template>
  <section class="mb-5 flex flex-wrap items-end justify-between gap-4">
    <div class="wall-surface">
      <p class="mono mb-1.5 text-xs tracking-wide text-muted">{{ dateLine }}</p>
      <h1 class="text-[28px] font-bold leading-tight text-ink sm:text-[32px]">今日信号</h1>
    </div>
    <div class="flex flex-wrap gap-2.5">
      <div v-if="newCount !== null" class="flex items-baseline gap-1.5 panel rounded-xl px-4 py-2.5">
        <span class="num text-[22px] font-bold text-ink">{{ newCount }}</span>
        <span class="text-[13px] text-muted">条新内容 · 24h</span>
      </div>
      <div class="flex items-baseline gap-1.5 panel rounded-xl px-4 py-2.5">
        <span class="num text-[22px] font-bold text-ink">{{ ongoing }}</span>
        <span class="text-[13px] text-muted">个活动进行中</span>
      </div>
      <NuxtLink v-if="sourcesTotal" to="/sources" class="flex items-center gap-2 panel rounded-xl px-4 py-2.5 transition-colors hover:border-faint/60">
        <span class="size-2 rounded-full" :class="sourcesOk === sourcesTotal ? 'bg-ok' : 'bg-warn'" aria-hidden="true" />
        <span class="num text-[22px] font-bold text-ink">{{ sourcesOk }}<span class="font-medium text-faint">/{{ sourcesTotal }}</span></span>
        <span class="text-[13px] text-muted">来源正常</span>
      </NuxtLink>
    </div>
  </section>
</template>
