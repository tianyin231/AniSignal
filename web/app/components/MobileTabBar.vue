<script setup lang="ts">
import { CalendarDays, House, Layers, Package, UserRound } from 'lucide-vue-next'
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

// 手机端底部主导航（lg 以下）：首页 / 日历 / 事件 / 商品 / 我的；栏目链接沿用当前分区视角
const route = useRoute()
const { user } = useAuth()

const scope = computed(() => {
  const query = String(route.query.community || '')
  if (GAME_COMMUNITIES.some(c => c.key === query)) return query
  return 'all'
})
const home = computed(() => {
  if (scope.value === 'all') return { path: '/' }
  if (scope.value === 'ai') return { path: '/ai' }
  return { path: '/', query: { community: scope.value } }
})

const tabs = computed(() => [
  { key: 'home', label: '首页', icon: House, to: home.value, active: route.path === '/' || route.path === '/ai' },
  { key: 'calendar', label: '日历', icon: CalendarDays, to: { path: '/calendar', query: { community: scope.value } }, active: route.path === '/calendar' },
  { key: 'events', label: '事件', icon: Layers, to: { path: '/events', query: { community: scope.value } }, active: route.path.startsWith('/events') },
  { key: 'products', label: '商品', icon: Package, to: { path: '/products', query: { community: scope.value } }, active: route.path.startsWith('/products') },
  { key: 'me', label: '我的', icon: UserRound, to: { path: user.value ? '/account' : '/login' }, active: route.path === '/account' || route.path === '/login' },
])
</script>

<template>
  <nav
    class="glass fixed inset-x-0 bottom-0 z-40 grid h-16 grid-cols-5 border-t border-line pb-[env(safe-area-inset-bottom)] lg:hidden"
    aria-label="主导航"
  >
    <NuxtLink
      v-for="tab in tabs"
      :key="tab.key"
      :to="tab.to"
      class="flex flex-col items-center justify-center gap-1 text-[11px] transition-colors"
      :class="tab.active ? 'font-semibold text-accent-ink' : 'text-faint hover:text-ink'"
      :aria-current="tab.active ? 'page' : undefined"
    >
      <component :is="tab.icon" :size="21" :stroke-width="tab.active ? 2.3 : 1.9" aria-hidden="true" />
      {{ tab.label }}
    </NuxtLink>
  </nav>
</template>
