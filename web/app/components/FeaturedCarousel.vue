<script setup lang="ts">
import { ArrowLeft, ArrowRight, Flame, ImageIcon, ShieldCheck } from 'lucide-vue-next'
import { platformMeta, type RawItem } from '~/utils/data'

const props = defineProps<{ items: RawItem[] }>()
const activeIndex = ref(0)
const paused = ref(false)
const reducedMotion = ref(false)
const pageHidden = ref(false)
const touchStartX = ref<number | null>(null)
const failedCoverId = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | undefined

const current = computed(() => props.items[activeIndex.value])
const hasMultiple = computed(() => props.items.length > 1)
const currentPlatform = computed(() => current.value ? platformMeta[current.value.platform] : null)
const isOfficial = computed(() => current.value?.identity === 'official')
const viewCount = computed(() => Number(current.value?.metrics?.view || 0))

function goTo(index: number) {
  if (!props.items.length) return
  activeIndex.value = (index + props.items.length) % props.items.length
  failedCoverId.value = null
  restartTimer()
}

function previous() {
  goTo(activeIndex.value - 1)
}

function next() {
  goTo(activeIndex.value + 1)
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

watch(() => props.items.map(item => item.id).join(','), () => {
  activeIndex.value = 0
  failedCoverId.value = null
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
    class="panel overflow-hidden"
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
    <Transition name="feature-fade" mode="out-in">
      <article :key="current.id" class="grid md:grid-cols-[minmax(0,1.45fr)_minmax(15rem,0.85fr)]">
        <div class="relative min-h-56 overflow-hidden bg-slate-100 md:min-h-72 dark:bg-zinc-900">
          <NuxtLink
            :to="`/contents/${current.id}`"
            class="group absolute inset-0 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500"
          >
            <img
              v-if="current.cover && failedCoverId !== current.id"
              :src="current.cover"
              :alt="`${current.title}封面`"
              referrerpolicy="no-referrer"
              loading="eager"
              fetchpriority="high"
              class="size-full object-cover transition-opacity duration-200 group-hover:opacity-95"
              @error="failedCoverId = current.id"
            >
            <div v-else class="absolute inset-0 grid place-items-center bg-gradient-to-br from-blue-100 via-sky-50 to-cyan-100 text-blue-300 dark:from-blue-950 dark:via-zinc-900 dark:to-cyan-950 dark:text-blue-700">
              <ImageIcon :size="52" aria-hidden="true" />
            </div>
            <div class="absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-black/65 to-transparent" aria-hidden="true" />
            <span class="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-blue-600/95 px-2.5 py-1 text-xs font-semibold text-white shadow-sm backdrop-blur-sm">
              <ShieldCheck v-if="isOfficial" :size="14" aria-hidden="true" />
              <Flame v-else :size="14" aria-hidden="true" />
              {{ isOfficial ? '官方精选' : '社区热门' }}
            </span>
            <span v-if="currentPlatform" class="absolute bottom-3 left-3 rounded-full bg-black/70 px-2.5 py-1 text-xs text-white backdrop-blur-sm">
              {{ currentPlatform.label }}
            </span>
          </NuxtLink>

          <template v-if="hasMultiple">
            <button
              type="button"
              class="absolute left-3 top-1/2 z-10 grid size-11 -translate-y-1/2 cursor-pointer place-items-center rounded-full bg-black/55 text-white backdrop-blur-sm transition-colors hover:bg-black/75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              aria-label="上一条精选情报"
              @click="previous"
            >
              <ArrowLeft :size="19" aria-hidden="true" />
            </button>
            <button
              type="button"
              class="absolute right-3 top-1/2 z-10 grid size-11 -translate-y-1/2 cursor-pointer place-items-center rounded-full bg-black/55 text-white backdrop-blur-sm transition-colors hover:bg-black/75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              aria-label="下一条精选情报"
              @click="next"
            >
              <ArrowRight :size="19" aria-hidden="true" />
            </button>
          </template>
        </div>

        <div class="flex min-h-64 min-w-0 flex-col p-4 sm:p-5 md:min-h-72">
          <p class="text-xs font-semibold tracking-wide text-blue-700 dark:text-blue-300">
            {{ isOfficial ? '近期官方精选' : '近期社区热门' }}
          </p>
          <NuxtLink
            :to="`/contents/${current.id}`"
            class="mt-2 rounded-sm text-xl font-semibold leading-snug text-slate-900 transition-colors hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-zinc-50 dark:hover:text-blue-300"
          >
            {{ current.title }}
          </NuxtLink>
          <p v-if="current.excerpt" class="mt-3 line-clamp-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
            {{ current.excerpt }}
          </p>
          <div v-if="current.keywords?.length" class="mt-3 flex flex-wrap gap-1.5">
            <span v-for="keyword in current.keywords.slice(0, 3)" :key="keyword" class="rounded bg-blue-500/10 px-2 py-1 text-[11px] text-blue-700 dark:text-blue-300">
              {{ keyword }}
            </span>
          </div>
          <div class="mt-auto flex items-end justify-between gap-4 pt-5">
            <p class="min-w-0 text-xs text-slate-500 dark:text-zinc-400">
              <span class="block truncate">{{ current.account }}</span>
              <span class="mono mt-0.5 block text-slate-400 dark:text-zinc-500">{{ current.publishedAt }}</span>
              <span v-if="!isOfficial && viewCount" class="mt-0.5 block font-medium text-orange-600 dark:text-orange-300">{{ formatViews(viewCount) }}</span>
            </p>
            <NuxtLink
              :to="`/contents/${current.id}`"
              class="inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-zinc-900"
            >
              查看详情
              <ArrowRight :size="15" aria-hidden="true" />
            </NuxtLink>
          </div>
        </div>
      </article>
    </Transition>

    <nav v-if="hasMultiple" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 dark:border-zinc-800" aria-label="精选情报分页">
      <span class="mono text-xs text-slate-400">{{ activeIndex + 1 }} / {{ items.length }}</span>
      <div class="flex items-center gap-1.5">
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

<style scoped>
.feature-fade-enter-active,
.feature-fade-leave-active {
  transition: opacity 180ms ease;
}

.feature-fade-enter-from,
.feature-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .feature-fade-enter-active,
  .feature-fade-leave-active {
    transition: none;
  }
}
</style>
