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
// 手机端三格并排、数字在上；sm 起改为横排胶囊
const pill = 'panel flex flex-col gap-0.5 rounded-xl px-3 py-2.5 sm:flex-row sm:items-baseline sm:gap-1.5 sm:px-4'
</script>

<template>
  <section class="mb-5 flex flex-wrap items-end justify-between gap-4">
    <div class="wall-surface">
      <p class="mono mb-1.5 text-xs tracking-wide text-muted">{{ dateLine }}</p>
      <h1 class="text-[28px] font-bold leading-tight text-ink sm:text-[32px]">今日信号</h1>
    </div>
    <div class="grid w-full grid-cols-3 gap-2 sm:flex sm:w-auto sm:gap-2.5">
      <div :class="pill">
        <span class="num text-xl font-bold text-ink sm:text-[22px]">{{ newCount ?? '—' }}</span>
        <span class="text-xs text-muted sm:text-[13px]">条新内容 · 24h</span>
      </div>
      <div :class="pill">
        <span class="num text-xl font-bold text-ink sm:text-[22px]">{{ ongoing }}</span>
        <span class="text-xs text-muted sm:text-[13px]">个活动进行中</span>
      </div>
      <NuxtLink v-if="sourcesTotal" to="/sources" :class="[pill, 'transition-colors hover:border-faint/60']">
        <span class="num flex items-center gap-1.5 text-xl font-bold text-ink sm:text-[22px]">
          <span class="size-2 rounded-full" :class="sourcesOk === sourcesTotal ? 'bg-ok' : 'bg-warn'" aria-hidden="true" />
          <span>{{ sourcesOk }}<span class="font-medium text-faint">/{{ sourcesTotal }}</span></span>
        </span>
        <span class="text-xs text-muted sm:text-[13px]">来源正常</span>
      </NuxtLink>
    </div>
  </section>
</template>
