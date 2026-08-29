<script setup lang="ts">
import { Radar } from 'lucide-vue-next'

// 账号中心专属布局：极简头部 + 宽容器，与公开站点/认证页分离
const { user, logout } = useAuth()

async function handleLogout() {
  await logout()
  navigateTo('/')
}
</script>

<template>
  <div class="aurora-bg min-h-screen">
    <WallpaperBackdrop />
    <AnnouncementBanner />
  <header class="glass sticky top-0 z-20">
      <div class="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
        <NuxtLink to="/" class="flex items-center gap-2">
          <span class="grid size-7 place-items-center rounded-md bg-blue-600 text-white">
            <Radar :size="15" />
          </span>
          <span class="text-[15px] font-semibold tracking-tight">AniSignal</span>
          <span class="chip ml-1 bg-blue-600/10 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300">账号中心</span>
        </NuxtLink>
        <div class="flex items-center gap-3 text-sm">
          <NuxtLink to="/" class="text-slate-500 transition-colors hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-100">
            返回站点
          </NuxtLink>
          <button class="rounded-md border border-slate-900/10 px-3 py-1.5 text-slate-600 transition-colors hover:bg-slate-900/5 dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/10" @click="handleLogout">
            退出登录
          </button>
        </div>
      </div>
    </header>
    <main class="mx-auto max-w-5xl px-6 py-8">
      <slot />
    </main>
  </div>
</template>
