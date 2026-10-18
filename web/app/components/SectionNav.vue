<script setup lang="ts">
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

/**
 * 栏目导航（总览 / 官方动态 / 活动日历 / 事件 / 商品追踪 / 内容库）。
 * 顶栏只有频道一层，栏目放在内容区首行、随页面滚动；默认插槽放在右侧（如首页的刷新按钮）。
 * 视角：URL 指定分区 → 该分区；聚合首页或 community=all → 全部分区；其余跟随分区状态源。
 */
const route = useRoute()
const { community } = useGameSection()

const scope = computed(() => {
  const query = String(route.query.community || '')
  if (GAME_COMMUNITIES.some(c => c.key === query)) return query
  if (query === 'all' || route.path === '/') return 'all'
  return community.value
})

const homeTo = computed(() => {
  if (scope.value === 'all') return { path: '/' }
  if (scope.value === 'ai') return { path: '/ai' }
  return { path: '/', query: { community: scope.value } }
})

const links = computed(() => [
  { key: 'home', label: '总览', to: homeTo.value, active: route.path === '/' || route.path === '/ai' },
  ...[
    { path: '/official', label: '官方动态' },
    { path: '/calendar', label: '活动日历' },
    { path: '/events', label: '事件' },
    { path: '/products', label: '商品追踪' },
    { path: '/contents', label: '内容库' },
  ].map(item => ({
    key: item.path,
    label: item.label,
    to: { path: item.path, query: { community: scope.value } },
    active: route.path.startsWith(item.path),
  })),
])
</script>

<template>
  <div class="wall-surface mb-5 flex items-center justify-between gap-3">
    <nav class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]" aria-label="站点栏目">
      <NuxtLink
        v-for="link in links"
        :key="link.key"
        :to="link.to"
        class="inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full px-3.5 text-sm transition-colors"
        :class="link.active ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-chip hover:text-ink'"
        :aria-current="link.active ? 'page' : undefined"
      >
        {{ link.label }}
      </NuxtLink>
    </nav>
    <div v-if="$slots.default" class="flex shrink-0 items-center gap-2">
      <slot />
    </div>
  </div>
</template>
