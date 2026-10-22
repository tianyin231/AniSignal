<script setup lang="ts">
import { channelThemeClass } from '~/composables/useGameSection'

const { active } = useActiveChannel()
const themeClass = computed(() => channelThemeClass(active.value))
</script>

<template>
  <div class="flex min-h-dvh flex-col pb-16 lg:pb-0" :class="themeClass">
    <AppTopBar />
    <AnnouncementBanner />
    <div class="mx-auto w-full max-w-[1280px] flex-1 px-4 lg:px-8">
      <slot />
    </div>
    <AppFooter />
    <MobileTabBar />
  </div>
</template>
