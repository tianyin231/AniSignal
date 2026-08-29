<script setup lang="ts">
import { ArrowLeft, ArrowRight, Flame, ImageIcon, ShieldCheck } from 'lucide-vue-next'
import { platformMeta, type RawItem } from '~/utils/data'

const props = defineProps<{ items: RawItem[] }>()
const activeIndex = ref(0)
const slideIndex = ref(props.items.length > 1 ? 1 : 0)
const transitionEnabled = ref(true)
const paused = ref(false)
const reducedMotion = ref(false)
const pageHidden = ref(false)
const touchStartX = ref<number | null>(null)
const failedCoverIds = ref<string[]>([])
let timer: ReturnType<typeof setInterval> | undefined
let wrapTimer: ReturnType<typeof setTimeout> | undefined

const current = computed(() => props.items[activeIndex.value])
const hasMultiple = computed(() => props.items.length > 1)
const loopItems = computed(() => hasMultiple.value
  ? [props.items.at(-1)!, ...props.items, props.items[0]!]
  : props.items)

function goTo(index: number) {
  if (!props.items.length || wrapTimer) return
  activeIndex.value = (index + props.items.length) % props.items.length
  slideIndex.value = hasMultiple.value ? activeIndex.value + 1 : activeIndex.value
  restartTimer()
}

function previous() {
  if (!hasMultiple.value || wrapTimer) return
  if (activeIndex.value === 0) {
    activeIndex.value = props.items.length - 1
    slideIndex.value = 0
    finishWrap(props.items.length)
  } else {
    activeIndex.value -= 1
    slideIndex.value -= 1
  }
  restartTimer()
}

function next() {
  if (!hasMultiple.value || wrapTimer) return
  if (activeIndex.value === props.items.length - 1) {
    activeIndex.value = 0
    slideIndex.value = props.items.length + 1
    finishWrap(1)
  } else {
    activeIndex.value += 1
    slideIndex.value += 1
  }
  restartTimer()
}

function finishWrap(index: number) {
  wrapTimer = setTimeout(() => {
    transitionEnabled.value = false
    slideIndex.value = index
    wrapTimer = undefined
    requestAnimationFrame(() => {
      transitionEnabled.value = true
    })
  }, 320)
}

function restartTimer() {
  if (timer) clearInterval(timer)
  timer = undefined
  if (!hasMultiple.value || paused.value || reducedMotion.value || pageHidden.value) return
  timer = setInterval(next, 6_000)
}

function onVisibilityChange() {
  pageHidden.value = document.visibilityState !== 'visible'
  restartTimer()
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
  const endX = event.changedTouches[0]?.clientX ?? touchStartX.value
  const distance = endX - touchStartX.value
  touchStartX.value = null
  if (Math.abs(distance) < 50) return
  distance > 0 ? previous() : next()
}

function formatViews(value: number) {
  if (value >= 10_000) return `${(value / 10_000).toFixed(value >= 100_000 ? 0 : 1)} 万播放`
  return `${value} 播放`
}

function markCoverFailed(id: string) {
  if (!failedCoverIds.value.includes(id)) failedCoverIds.value.push(id)
}

watch(() => props.items.map(item => item.id).join(','), () => {
  if (wrapTimer) clearTimeout(wrapTimer)
  wrapTimer = undefined
  activeIndex.value = 0
  slideIndex.value = props.items.length > 1 ? 1 : 0
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
  if (wrapTimer) clearTimeout(wrapTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <section
    v-if="current"
    class="group/carousel panel overflow-hidden"
    role="region"
    aria-roledescription="轮播"
    aria-label="精选情报"
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
    <div class="relative h-[31rem] overflow-hidden md:h-72">
      <div
        class="flex h-full will-change-transform"
        :class="transitionEnabled ? 'transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none' : 'transition-none'"
        :style="{ transform: `translate3d(-${slideIndex * 100}%, 0, 0)` }"
      >
        <article
          v-for="(item, index) in loopItems"
          :key="`${item.id}-${index}`"
          class="grid h-full min-w-full grid-rows-[14rem_17rem] md:grid-cols-[minmax(0,1.45fr)_minmax(15rem,0.85fr)] md:grid-rows-none"
          :aria-hidden="index !== slideIndex"
        >
          <div class="relative h-full overflow-hidden bg-slate-100 dark:bg-zinc-900">
          <NuxtLink
            :to="`/contents/${item.id}`"
            class="group absolute inset-0 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500"
            :tabindex="index === slideIndex ? undefined : -1"
          >
            <img
              v-if="item.cover && !failedCoverIds.includes(item.id)"
              :src="item.cover"
              :alt="`${item.title}封面`"
              referrerpolicy="no-referrer"
              :loading="index === slideIndex ? 'eager' : 'lazy'"
              :fetchpriority="index === slideIndex ? 'high' : 'auto'"
              class="size-full object-cover transition-opacity duration-200 group-hover:opacity-95"
              @error="markCoverFailed(item.id)"
            >
            <div v-else class="absolute inset-0 grid place-items-center bg-gradient-to-br from-blue-100 via-sky-50 to-cyan-100 text-blue-300 dark:from-blue-950 dark:via-zinc-900 dark:to-cyan-950 dark:text-blue-700">
              <ImageIcon :size="52" aria-hidden="true" />
            </div>
            <div class="absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-black/65 to-transparent" aria-hidden="true" />
            <span class="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-blue-600/95 px-2.5 py-1 text-xs font-semibold text-white shadow-sm backdrop-blur-sm">
              <ShieldCheck v-if="item.identity === 'official'" :size="14" aria-hidden="true" />
              <Flame v-else :size="14" aria-hidden="true" />
              {{ item.identity === 'official' ? '官方精选' : '社区热门' }}
            </span>
            <span v-if="platformMeta[item.platform]" class="absolute bottom-3 left-3 rounded-full bg-black/70 px-2.5 py-1 text-xs text-white backdrop-blur-sm">
              {{ platformMeta[item.platform].label }}
            </span>
          </NuxtLink>
          </div>

          <div class="flex h-full min-w-0 flex-col overflow-hidden p-4 sm:p-5">
          <p class="text-xs font-semibold tracking-wide text-blue-700 dark:text-blue-300">
            {{ item.identity === 'official' ? '近期官方精选' : '近期社区热门' }}
          </p>
          <NuxtLink
            :to="`/contents/${item.id}`"
            class="mt-2 line-clamp-2 rounded-sm text-xl font-semibold leading-snug text-slate-900 transition-colors hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-zinc-50 dark:hover:text-blue-300"
            :tabindex="index === slideIndex ? undefined : -1"
          >
            {{ item.title }}
          </NuxtLink>
          <p v-if="item.excerpt" class="mt-3 line-clamp-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
            {{ item.excerpt }}
          </p>
          <div v-if="item.keywords?.length" class="mt-3 flex flex-wrap gap-1.5">
            <span v-for="keyword in item.keywords.slice(0, 3)" :key="keyword" class="rounded bg-blue-500/10 px-2 py-1 text-[11px] text-blue-700 dark:text-blue-300">
              {{ keyword }}
            </span>
          </div>
          <div class="mt-auto flex items-end justify-between gap-4 pt-5">
            <p class="min-w-0 text-xs text-slate-500 dark:text-zinc-400">
              <span class="block truncate">{{ item.account }}</span>
              <span class="mono mt-0.5 block text-slate-400 dark:text-zinc-500">{{ item.publishedAt }}</span>
              <span v-if="item.identity !== 'official' && item.metrics?.view" class="mt-0.5 block font-medium text-orange-600 dark:text-orange-300">{{ formatViews(Number(item.metrics.view)) }}</span>
            </p>
            <NuxtLink
              :to="`/contents/${item.id}`"
              class="inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-zinc-900"
              :tabindex="index === slideIndex ? undefined : -1"
            >
              查看详情
              <ArrowRight :size="15" aria-hidden="true" />
            </NuxtLink>
          </div>
          </div>
        </article>
      </div>

      <template v-if="hasMultiple">
        <button
          type="button"
          class="pointer-events-none absolute left-3 top-28 z-10 grid size-11 -translate-y-1/2 cursor-pointer place-items-center rounded-xl border border-white/70 bg-white/85 text-slate-700 opacity-0 shadow-lg shadow-slate-900/10 backdrop-blur-md transition-[opacity,background-color,color] duration-200 hover:bg-white focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 group-focus-within/carousel:pointer-events-auto group-focus-within/carousel:opacity-100 group-hover/carousel:pointer-events-auto group-hover/carousel:opacity-100 md:top-1/2 dark:border-white/10 dark:bg-zinc-900/80 dark:text-zinc-100 dark:hover:bg-zinc-800/90"
          aria-label="上一条精选情报"
          @click="previous"
        >
          <ArrowLeft :size="18" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="pointer-events-none absolute right-3 top-28 z-10 grid size-11 -translate-y-1/2 cursor-pointer place-items-center rounded-xl border border-white/70 bg-white/85 text-slate-700 opacity-0 shadow-lg shadow-slate-900/10 backdrop-blur-md transition-[opacity,background-color,color] duration-200 hover:bg-white focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 group-focus-within/carousel:pointer-events-auto group-focus-within/carousel:opacity-100 group-hover/carousel:pointer-events-auto group-hover/carousel:opacity-100 md:top-1/2 dark:border-white/10 dark:bg-zinc-900/80 dark:text-zinc-100 dark:hover:bg-zinc-800/90"
          aria-label="下一条精选情报"
          @click="next"
        >
          <ArrowRight :size="18" aria-hidden="true" />
        </button>
      </template>
    </div>

    <nav v-if="hasMultiple" class="flex items-center justify-center border-t border-slate-200/80 px-4 py-2.5 dark:border-zinc-800" aria-label="精选情报分页">
      <div class="flex items-center gap-1">
        <button
          v-for="(item, index) in items"
          :key="item.id"
          type="button"
          class="grid size-11 cursor-pointer place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          :aria-label="`查看第 ${index + 1} 条精选情报`"
          :aria-current="index === activeIndex ? 'true' : undefined"
          @click="goTo(index)"
        >
          <span class="h-1.5 rounded-full transition-all duration-200" :class="index === activeIndex ? 'w-5 bg-blue-600' : 'w-1.5 bg-slate-300 dark:bg-zinc-600'" />
        </button>
      </div>
    </nav>
  </section>
</template>
