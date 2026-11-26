<script setup lang="ts">
import { imageSrcset, imageUrl } from '~/utils/image'

/**
 * 统一信号台深色频道头图基座：
 * 被游戏频道（ChannelHero）、AI 快讯（/ai）与所有主题流（TopicChannelHero）共同复用，
 * 保证全站圆角、暗底、封面渐变遮罩、光晕与移动端响应式表现完全一致。
 */
const props = withDefaults(defineProps<{
  ariaLabel: string
  cover?: string
  coverWidth?: 'wide' | 'half'
  accentHex?: string
  themeClass?: string
  hasAside?: boolean
}>(), {
  cover: '',
  coverWidth: 'half',
  accentHex: '',
  themeClass: '',
  hasAside: false,
})

const coverFailed = ref(false)
watch(() => props.cover, () => { coverFailed.value = false })
const showCover = computed(() => Boolean(props.cover && !coverFailed.value))
</script>

<template>
  <section
    class="dark relative isolate overflow-hidden rounded-[20px] bg-[#0c0d11] text-white shadow-card"
    :class="themeClass"
    :aria-label="ariaLabel"
  >
    <!-- 右侧封面图 + 统一柔和暗化与渐隐遮罩 -->
    <div
      v-if="showCover"
      class="relative w-full overflow-hidden sm:absolute sm:inset-y-0 sm:right-0 sm:-z-10"
      :class="coverWidth === 'wide' ? 'sm:w-[68%]' : 'sm:w-[46%]'"
    >
      <img
        :src="imageUrl(cover, 1600)"
        :srcset="imageSrcset(cover, [640, 960, 1600])"
        :sizes="coverWidth === 'wide' ? '(min-width: 1280px) 827px, (min-width: 640px) 68vw, 100vw' : '(min-width: 640px) 46vw, 100vw'"
        alt=""
        decoding="async"
        fetchpriority="high"
        referrerpolicy="no-referrer"
        class="aspect-[16/9] w-full object-cover sm:aspect-auto sm:h-full"
        :class="coverWidth === 'wide' ? 'opacity-70' : 'opacity-60'"
        @error="coverFailed = true"
      >
      <div
        v-if="coverWidth === 'wide'"
        class="absolute inset-0 bg-[linear-gradient(90deg,#0c0d11_30%,rgb(12_13_17/0.85)_46%,rgb(12_13_17/0.15)_80%,rgb(12_13_17/0.35)_100%)] max-sm:bg-[linear-gradient(180deg,rgb(12_13_17/0.35)_0%,#0c0d11_75%)]"
        aria-hidden="true"
      />
      <template v-else>
        <div class="absolute inset-0 bg-[#0c0d11]/35" aria-hidden="true" />
        <div class="absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-[#0c0d11] to-transparent sm:hidden" aria-hidden="true" />
        <div class="absolute inset-y-0 left-0 hidden w-36 bg-gradient-to-r from-[#0c0d11] via-[#0c0d11]/85 to-transparent sm:block" aria-hidden="true" />
      </template>
    </div>

    <!-- 无封面图时的频道主题柔光 -->
    <div
      v-else
      class="pointer-events-none absolute inset-0 -z-10"
      :style="accentHex
        ? { background: `radial-gradient(65% 95% at 100% 0%, ${accentHex}45, transparent 62%), radial-gradient(40% 60% at 15% 100%, ${accentHex}22, transparent 70%)` }
        : { background: 'radial-gradient(70% 90% at 100% 0%, color-mix(in srgb, var(--sd-accent) 42%, transparent), transparent 58%)' }"
      aria-hidden="true"
    />

    <!-- 内容布局：支持右侧独立卡片插槽或封面并排 -->
    <div
      class="relative px-5 py-6 sm:px-8 sm:py-8"
      :class="hasAside && !showCover ? 'grid items-center gap-6 lg:grid-cols-[minmax(0,1fr)_320px]' : ''"
    >
      <div class="min-w-0" :class="showCover && coverWidth === 'half' ? 'sm:max-w-[54%]' : ''">
        <slot />
      </div>
      <slot v-if="hasAside && !showCover" name="aside" />
    </div>
  </section>
</template>
