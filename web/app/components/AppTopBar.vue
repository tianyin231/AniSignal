<script setup lang="ts">
import { avatarGradient, displayName } from '~/utils/avatars'

const route = useRoute()
const links = [
  { to: '/', label: '首页' },
  { to: '/official', label: '官方动态' },
  { to: '/products', label: '商品' },
  { to: '/events', label: '事件' },
  { to: '/contents', label: '内容库' },
]
const isActive = (to: string) => (to === '/' ? route.path === '/' : route.path.startsWith(to))

const { user, logout } = useAuth()
const menuOpen = ref(false)
const menuRoot = ref<HTMLElement | null>(null)

function onDocClick(event: MouseEvent) {
  if (menuRoot.value && !menuRoot.value.contains(event.target as Node)) menuOpen.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

async function handleLogout() {
  menuOpen.value = false
  await logout()
  navigateTo('/')
}
</script>

<template>
  <header class="glass sticky top-0 z-40">
    <div class="mx-auto flex h-14 max-w-[1280px] items-center gap-4 px-4 lg:px-6">
      <NuxtLink to="/" class="flex shrink-0 items-center gap-2">
        <img src="/icon.png" alt="" class="size-7 shrink-0">
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
        <WallpaperToggle />
        <ThemeToggle />
        <NuxtLink
          v-if="!user"
          to="/login"
          class="btn-primary ml-1 hidden px-3.5 py-1.5 text-xs sm:block"
        >
          登录
        </NuxtLink>
        <div v-else ref="menuRoot" class="relative">
          <button
            class="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-slate-200/70 dark:hover:bg-zinc-800/70"
            @click="menuOpen = !menuOpen"
          >
            <span class="grid size-6 place-items-center rounded-full bg-gradient-to-b text-[11px] font-semibold text-white" :class="avatarGradient(user.avatar)">
              {{ displayName(user)[0]!.toUpperCase() }}
            </span>
            <span class="hidden max-w-32 truncate text-xs text-slate-600 sm:block dark:text-zinc-300">{{ displayName(user) }}</span>
          </button>
          <div v-if="menuOpen" class="popover absolute right-0 top-full z-50 mt-1.5 w-44 p-1.5">
            <NuxtLink to="/account" class="block rounded-md px-3 py-2 text-sm text-slate-700 hover:bg-slate-500/10 dark:text-zinc-200" @click="menuOpen = false">
              账号中心
            </NuxtLink>
            <NuxtLink to="/watch" class="block rounded-md px-3 py-2 text-sm text-slate-700 hover:bg-slate-500/10 dark:text-zinc-200" @click="menuOpen = false">
              订阅中心
            </NuxtLink>
            <button class="block w-full rounded-md px-3 py-2 text-left text-sm text-red-500 hover:bg-red-500/10 dark:text-red-400" @click="handleLogout">
              退出登录
            </button>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>
