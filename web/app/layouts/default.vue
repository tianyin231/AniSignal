<template>
  <div class="min-h-screen pb-16 lg:pb-0" :class="themeClass">
    <WallpaperBackdrop />
    <AppTopBar />
    <AnnouncementBanner />
    <div class="mx-auto max-w-[1280px] px-4 lg:px-8">
      <div class="grid gap-6 py-6 lg:py-8" :class="{ 'xl:grid-cols-[minmax(0,1fr)_300px]': showRail }">
        <main class="min-w-0">
          <slot />
        </main>
        <aside v-if="showRail" class="hidden xl:block">
          <div class="sticky top-[88px] space-y-4">
            <RightRail />
          </div>
        </aside>
      </div>
    </div>
    <AppFooter />
    <MobileTabBar />
  </div>
</template>

<script setup lang="ts">
import { channelThemeClass } from '~/composables/useGameSection'

const route = useRoute()
// 首页自带侧栏区块；统计 / 日历 / 内容详情 / AI 快讯需要整宽
const showRail = computed(() =>
  !['/', '/ai', '/stats', '/calendar'].includes(route.path) && !/^\/contents\/[^/]+$/.test(route.path),
)
// 频道主题：进入游戏频道后，accent 令牌切换为该游戏的品牌色
const { active } = useActiveChannel()
const themeClass = computed(() => channelThemeClass(active.value))
</script>
