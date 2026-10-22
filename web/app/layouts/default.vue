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
// 首页自带侧栏区块；统计 / 日历 / 内容详情 / AI 快讯需要整宽
const showRail = computed(() =>
  !['/', '/ai', '/stats', '/calendar'].includes(route.path) && !route.path.startsWith('/ai/') && !/^\/contents\/[^/]+$/.test(route.path),
)
</script>
