<script setup lang="ts">
import { BadgeCheck, Flame, ImageIcon } from 'lucide-vue-next'
import { platformMeta, type RawItem } from '~/utils/data'
import { communityClasses, communityMeta } from '~/composables/useGameSection'

/**
 * 首页精选大图：整卡封面 + 底部压暗标题，右下角缩略图切换（手机端为圆点）。
 * 自动轮播 6 秒；悬停 / 聚焦 / 页面隐藏 / 减少动态效果时暂停；支持左右方向键与触屏滑动。
 */
const props = defineProps<{ items: RawItem[]; showCommunity?: boolean }>()
const active = ref(0)
const paused = ref(false)
const reducedMotion = ref(false)
const pageHidden = ref(false)
const touchStartX = ref<number | null>(null)
const failedCoverIds = ref<string[]>([])
let timer: ReturnType<typeof setInterval> | undefined

const current = computed(() => props.items[active.value])
const hasMultiple = computed(() => props.items.length > 1)
const currentCommunity = computed(() => (props.showCommunity ? communityMeta(current.value?.community) : undefined))
const excerptLine = computed(() => current.value?.excerpt?.replace(/\s+/g, ' ').trim() ?? '')

function formatViews(value: number) {
  if (value >= 10_000) return `${(value / 10_000).toFixed(value >= 100_000 ? 0 : 1)} 万播放`
  return `${value} 播放`
}
const meta = computed(() => {
  const item = current.value
  if (!item) return ''
  const parts = [platformMeta[item.platform]?.label, item.account, item.publishedAt]
  const views = Number(item.metrics?.view ?? 0)
  if (views > 0) parts.push(formatViews(views))
  return parts.filter(Boolean).join(' · ')
})

function goTo(index: number) {
  if (!props.items.length) return
  active.value = (index + props.items.length) % props.items.length
  restartTimer()
}
const next = () => hasMultiple.value && goTo(active.value + 1)
const previous = () => hasMultiple.value && goTo(active.value - 1)

function restartTimer() {
  if (timer) clearInterval(timer)
  timer = undefined
  if (!hasMultiple.value || paused.value || reducedMotion.value || pageHidden.value) return
  timer = setInterval(() => goTo(active.value + 1), 6_000)
}

function onVisibilityChange() {
  pageHidden.value = document.visibilityState !== 'visible'
}
function onFocusOut(event: FocusEvent) {
  const root = event.currentTarget as HTMLElement
  if (event.relatedTarget instanceof Node && root.contains(event.relatedTarget)) return
  paused.value = false
}
function onTouchStart(event: TouchEvent) {
  touchStartX.value = event.touches[0]?.clientX ?? null
}
function onTouchEnd(event: TouchEvent) {
  if (touchStartX.value === null) return
  const distance = (event.changedTouches[0]?.clientX ?? touchStartX.value) - touchStartX.value
  touchStartX.value = null
  if (Math.abs(distance) < 50) return
  if (distance > 0) previous()
  else next()
}
function markCoverFailed(id: string) {
  if (!failedCoverIds.value.includes(id)) failedCoverIds.value.push(id)
}

watch(() => props.items.map(item => item.id).join(','), () => {
  active.value = 0
  failedCoverIds.value = []
  restartTimer()
})
watch([paused, reducedMotion, pageHidden], restartTimer)

onMounted(() => {
  reducedMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  document.addEventListener('visibilitychange', onVisibilityChange)
  restartTimer()
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <section
    v-if="current"
    class="relative isolate min-h-[340px] overflow-hidden rounded-[20px] bg-[#0b0f17] shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand sm:min-h-[440px]"
    role="region"
    aria-roledescription="轮播"
    aria-label="精选动态"
    tabindex="0"
    @mouseenter="paused = true"
    @mouseleave="paused = false"
    @focusin="paused = true"
    @focusout="onFocusOut"
    @keydown.left.prevent="previous"
    @keydown.right.prevent="next"
    @touchstart.passive="onTouchStart"
    @touchend.passive="onTouchEnd"
  >
    <NuxtLink
      v-for="(item, index) in items"
      :key="item.id"
      :to="`/contents/${item.id}`"
      class="absolute inset-0 transition-opacity duration-500 ease-out motion-reduce:transition-none"
      :class="index === active ? 'z-[1] opacity-100' : 'pointer-events-none opacity-0'"
      :tabindex="index === active ? undefined : -1"
      :aria-hidden="index !== active"
      :aria-label="item.title"
    >
      <img
        v-if="item.cover && !failedCoverIds.includes(item.id)"
        :src="item.cover"
        alt=""
        referrerpolicy="no-referrer"
        :loading="index === 0 ? 'eager' : 'lazy'"
        :fetchpriority="index === 0 ? 'high' : 'auto'"
        class="size-full object-cover"
        @error="markCoverFailed(item.id)"
      >
      <div v-else class="grid size-full place-items-center bg-gradient-to-br from-slate-700 via-slate-800 to-slate-950 text-white/25">
        <ImageIcon :size="56" aria-hidden="true" />
      </div>
    </NuxtLink>
    <!-- 底部压暗：保证白字在任意封面上可读 -->
    <div class="pointer-events-none absolute inset-0 z-[2] bg-[linear-gradient(180deg,rgb(6_10_18/0)_30%,rgb(6_10_18/0.6)_62%,rgb(6_10_18/0.94)_100%)]" aria-hidden="true" />

    <div class="pointer-events-none absolute inset-x-0 bottom-0 z-[3] flex flex-col gap-2.5 p-5 text-white sm:p-7">
      <div class="flex flex-wrap gap-2">
        <span v-if="current.identity === 'official'" class="inline-flex h-[26px] items-center gap-1 rounded-[7px] bg-white px-2.5 text-xs font-bold text-[#1c45d0]">
          <BadgeCheck :size="14" aria-hidden="true" />官方
        </span>
        <span v-else class="inline-flex h-[26px] items-center gap-1 rounded-[7px] bg-black/55 px-2.5 text-xs font-semibold backdrop-blur-sm">
          <Flame :size="14" class="text-orange-300" aria-hidden="true" />社区热门
        </span>
        <span v-if="currentCommunity" class="inline-flex h-[26px] items-center gap-1.5 rounded-[7px] bg-black/55 px-2.5 text-xs font-semibold backdrop-blur-sm">
          <span class="size-[7px] rounded-full" :class="communityClasses(currentCommunity.color).dot" aria-hidden="true" />
          {{ currentCommunity.label }}
        </span>
      </div>
      <h2 class="line-clamp-2 max-w-[44rem] text-xl font-bold leading-snug sm:text-[28px] sm:leading-tight">{{ current.title }}</h2>
      <p v-if="excerptLine" class="hidden max-w-[40rem] truncate text-sm text-white/80 sm:block">{{ excerptLine }}</p>
      <div class="mt-1 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <p class="mono min-w-0 text-xs text-white/75">{{ meta }}</p>
        <div v-if="hasMultiple" class="pointer-events-auto hidden gap-1.5 sm:flex" role="group" aria-label="切换精选">
          <button
            v-for="(item, index) in items"
            :key="item.id"
            type="button"
            class="h-11 w-[72px] overflow-hidden rounded-lg border-2 bg-black transition-opacity"
            :class="index === active ? 'border-white opacity-100' : 'border-transparent opacity-55 hover:opacity-90'"
            :aria-label="`查看第 ${index + 1} 条精选：${item.title}`"
            :aria-current="index === active ? 'true' : undefined"
            @click="goTo(index)"
          >
            <img v-if="item.cover && !failedCoverIds.includes(item.id)" :src="item.cover" alt="" referrerpolicy="no-referrer" loading="lazy" class="size-full object-cover">
          </button>
        </div>
        <div v-if="hasMultiple" class="pointer-events-auto flex gap-0.5 sm:hidden" role="group" aria-label="切换精选">
          <button
            v-for="(item, index) in items"
            :key="item.id"
            type="button"
            class="grid size-7 place-items-center"
            :aria-label="`查看第 ${index + 1} 条精选`"
            :aria-current="index === active ? 'true' : undefined"
            @click="goTo(index)"
          >
            <span class="h-1 rounded-full transition-all" :class="index === active ? 'w-4 bg-white' : 'w-1 bg-white/50'" />
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
