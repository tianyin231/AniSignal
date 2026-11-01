<template>
  <AppShell>
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
  </AppShell>
</template>

<script setup lang="ts">
import AppShell from '~/components/AppShell.vue'

const route = useRoute()
// 首页自带侧栏区块；统计 / 日历 / 内容详情 / AI 快讯 / 订阅中心 / 定制化需要整宽（后两者有自己的侧栏）
const showRail = computed(() =>
  !['/', '/ai', '/stats', '/calendar', '/watch'].includes(route.path)
  && !['/ai/', '/products', '/custom'].some(prefix => route.path.startsWith(prefix))
  && !/^\/contents\/[^/]+$/.test(route.path),
)
</script>
