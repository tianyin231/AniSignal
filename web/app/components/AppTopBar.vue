<script setup lang="ts">
import { Radar } from 'lucide-vue-next'

const route = useRoute()
const links = [
  { to: '/', label: '首页' },
  { to: '/official', label: '官方动态' },
  { to: '/products', label: '商品' },
  { to: '/events', label: '事件' },
]
const isActive = (to: string) => (to === '/' ? route.path === '/' : route.path.startsWith(to))
</script>

<template>
  <header class="glass sticky top-0 z-40">
    <div class="mx-auto flex h-14 max-w-[1280px] items-center gap-4 px-4 lg:px-6">
      <NuxtLink to="/" class="flex shrink-0 items-center gap-2">
        <span class="grid size-7 place-items-center rounded-md bg-blue-600 text-white">
          <Radar :size="15" />
        </span>
        <span class="text-[15px] font-semibold tracking-tight text-slate-900 dark:text-zinc-50">AniSignal</span>
      </NuxtLink>
      <nav class="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto text-sm [scrollbar-width:none]">
        <NuxtLink
          v-for="l in links"
          :key="l.to"
          :to="l.to"
          class="whitespace-nowrap rounded-md px-2.5 py-1.5 transition-colors"
          :class="
            isActive(l.to)
              ? 'bg-blue-500/10 font-medium text-blue-600 dark:bg-blue-400/15 dark:text-blue-300'
              : 'text-slate-600 hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100'
          "
        >
          {{ l.label }}
        </NuxtLink>
      </nav>
      <div class="flex shrink-0 items-center gap-1">
        <SearchBox />
        <NotificationBell />
        <ThemeToggle />
        <AccountMenu />
      </div>
    </div>
  </header>
</template>
