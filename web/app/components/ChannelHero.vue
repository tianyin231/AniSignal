<script setup lang="ts">
import { ArrowUpRight } from 'lucide-vue-next'
import { channelThemeClass, communityClasses, communityMeta } from '~/composables/useGameSection'

/**
 * 游戏频道头图：深色底 + 右侧主视觉（最新精选封面），左侧游戏名、核心数字与官方入口。
 * 根节点固定挂 .dark + .ch-<分区>：不论站点处于浅色还是深色，头图内都用深色令牌与该游戏的强调色。
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
const coverFailed = ref(false)
watch(() => props.cover, () => { coverFailed.value = false })

const key = computed(() => props.communityKey)
const { items: links, fetchOnce } = useOfficialLinks(key)
onMounted(() => fetchOnce())
watch(key, () => fetchOnce())
const quickLinks = computed(() => links.value.slice(0, 6))
</script>

<template>
  <section
    v-if="meta"
    class="dark relative isolate mb-5 overflow-hidden rounded-[20px] bg-[#0c0d11] text-ink shadow-card"
    :class="channelThemeClass(communityKey)"
    :aria-label="`${meta.label}频道`"
  >
    <img
      v-if="cover && !coverFailed"
      :src="cover"
      alt=""
      referrerpolicy="no-referrer"
      class="absolute inset-y-0 right-0 -z-10 h-full w-full object-cover opacity-70 sm:w-[68%]"
      @error="coverFailed = true"
    >
    <div class="absolute inset-0 -z-10 bg-[linear-gradient(90deg,#0c0d11_30%,rgb(12_13_17/0.85)_46%,rgb(12_13_17/0.15)_80%,rgb(12_13_17/0.35)_100%)] max-sm:bg-[linear-gradient(180deg,rgb(12_13_17/0.35)_0%,#0c0d11_75%)]" aria-hidden="true" />
    <div class="flex flex-col gap-4 px-6 pb-7 pt-10 sm:px-9 sm:pt-12">
      <p class="num text-xs font-medium uppercase tracking-[0.28em]" :class="tone.text">{{ meta.en }}</p>
      <h1 class="text-[40px] font-extrabold leading-none tracking-wide text-white sm:text-[56px]">{{ meta.label }}</h1>
      <NuxtLink v-if="latest" :to="latest.to" class="max-w-xl truncate text-[15px] text-white/85 hover:text-white">
        最新：{{ latest.title }}
      </NuxtLink>
      <div class="mt-1 flex flex-wrap gap-x-7 gap-y-2">
        <p><span class="num text-3xl font-bold" :class="tone.text">{{ ongoing }}</span><span class="ml-1.5 text-[13px] text-white/70">个活动进行中</span></p>
        <p><span class="num text-3xl font-bold" :class="tone.text">{{ upcoming }}</span><span class="ml-1.5 text-[13px] text-white/70">项即将开始 / 截止</span></p>
        <p v-if="newCount !== null"><span class="num text-3xl font-bold" :class="tone.text">{{ newCount }}</span><span class="ml-1.5 text-[13px] text-white/70">条新内容 · 24h</span></p>
      </div>
      <!-- 手机端横向滑动一行，桌面端换行平铺 -->
      <nav v-if="quickLinks.length" class="-mx-6 mt-1 flex gap-2 overflow-x-auto px-6 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:px-0" :aria-label="`${meta.label}官方入口`">
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
  </section>
</template>
