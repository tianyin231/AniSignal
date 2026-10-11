<script setup lang="ts">
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

const route = useRoute()
const calendarTo = computed(() => ({
  path: '/calendar',
  query: { community: GAME_COMMUNITIES.some(c => c.key === route.query.community) ? String(route.query.community) : 'all' },
}))
// 顶栏承载内容主栏目；侧栏只放二级页面（商品入口在顶栏与右栏）
const tools = computed(() => [
  { to: calendarTo.value, label: '活动日历' },
  { to: '/stats', label: '数据统计' },
  { to: '/sources', label: '来源健康' },
  { to: '/watch', label: '订阅中心' },
])

</script>

<template>
  <div class="space-y-4">
    <nav class="panel-glass space-y-5 p-3 text-sm" aria-label="工具">
      <div>
        <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">工具</p>
        <ul class="space-y-0.5">
          <li v-for="q in tools" :key="q.label">
            <NuxtLink
              :to="q.to"
              :prefetch-on="q.to === '/stats' ? 'interaction' : 'visibility'"
              class="block truncate rounded-md px-2 py-1.5 text-slate-600 transition-colors hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
            >
              {{ q.label }}
            </NuxtLink>
          </li>
        </ul>
      </div>
    </nav>
    <section class="panel-glass p-3 text-sm" aria-label="官方链接">
      <OfficialLinks variant="list" />
    </section>
  </div>
</template>
