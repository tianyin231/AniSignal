<script setup lang="ts">
import { ArrowUpRight } from 'lucide-vue-next'
import { channelThemeClass, communityClasses, communityMeta } from '~/composables/useGameSection'

/**
 * 游戏频道头图：基于统一 SignalHero 基座，展示游戏名、核心数字与官方入口。
 */
const props = defineProps<{
  communityKey: string
  cover?: string
  newCount: number | null
  ongoing: number
  upcoming: number
  latest?: { title: string, to: string } | null
}>()

const meta = computed(() => communityMeta(props.communityKey))
const tone = computed(() => communityClasses(meta.value?.color ?? 'blue'))

const key = computed(() => props.communityKey)
const { items: links, fetchOnce } = useOfficialLinks(key)
onMounted(() => fetchOnce())
watch(key, () => fetchOnce())
const quickLinks = computed(() => links.value.slice(0, 6))
</script>

<template>
  <SignalHero
    v-if="meta"
    class="mb-5"
    :aria-label="`${meta.label}频道`"
    :cover="cover"
    cover-width="wide"
    :theme-class="channelThemeClass(communityKey)"
  >
    <div class="flex flex-col gap-4 py-2 sm:py-3">
      <p class="num text-xs font-medium uppercase tracking-[0.28em]" :class="tone.text">{{ meta.en }}</p>
      <h1 class="text-[40px] font-extrabold leading-none tracking-wide text-white sm:text-[56px]">{{ meta.label }}</h1>
      <div class="h-6 min-w-0">
        <NuxtLink v-if="latest" :to="latest.to" class="block max-w-xl truncate text-[15px] text-white/85 hover:text-white">
          最新：{{ latest.title }}
        </NuxtLink>
      </div>
      <div class="mt-1 flex flex-wrap gap-x-7 gap-y-2">
        <p><span class="num text-3xl font-bold" :class="tone.text">{{ ongoing }}</span><span class="ml-1.5 text-[13px] text-white/70">个活动进行中</span></p>
        <p><span class="num text-3xl font-bold" :class="tone.text">{{ upcoming }}</span><span class="ml-1.5 text-[13px] text-white/70">项即将开始 / 截止</span></p>
        <p><span class="num text-3xl font-bold" :class="tone.text">{{ newCount ?? '—' }}</span><span class="ml-1.5 text-[13px] text-white/70">条新内容 · 24h</span></p>
      </div>
      <!-- 始终预留一行入口空间；过长时横滑，避免异步加载或换行改变头图高度。 -->
      <nav class="-mx-5 mt-1 flex h-9 gap-2 overflow-x-auto px-5 [scrollbar-width:none] sm:mx-0 sm:px-0" :aria-label="`${meta.label}官方入口`">
        <a
          v-for="link in quickLinks"
          :key="link.url"
          :href="link.url"
          target="_blank"
          rel="noopener"
          class="inline-flex h-9 max-w-[16rem] shrink-0 items-center gap-1.5 rounded-full border border-white/20 bg-white/5 px-3.5 text-[13px] text-white/90 transition-colors hover:border-accent hover:text-accent-ink"
        >
          <span class="truncate">{{ link.label || link.title }}</span>
          <ArrowUpRight :size="13" class="shrink-0" aria-hidden="true" />
        </a>
      </nav>
    </div>
  </SignalHero>
</template>
