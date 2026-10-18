<script setup lang="ts">

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
  <header class="glass sticky top-0 z-20">
      <div class="mx-auto flex h-16 max-w-5xl items-center justify-between px-4 sm:px-6">
        <NuxtLink to="/" class="flex items-center gap-2">
          <img src="/icon.png" alt="" class="size-8 shrink-0 rounded-[9px]">
          <span class="num text-[19px] font-bold tracking-tight text-ink">AniSignal</span>
          <span class="ml-1 rounded-md bg-brand-soft px-2 py-0.5 text-xs font-semibold text-brand-ink">账号中心</span>
        </NuxtLink>
        <div class="flex items-center gap-3 text-sm">
          <NuxtLink to="/" class="text-muted transition-colors hover:text-ink">
            返回站点
          </NuxtLink>
          <button class="h-9 rounded-[10px] border border-line px-3.5 text-ink-2 transition-colors hover:bg-chip" @click="handleLogout">
            退出登录
          </button>
        </div>
      </div>
    </header>
    <AnnouncementBanner />
    <main class="mx-auto max-w-5xl px-6 py-8">
      <slot />
    </main>
  </div>
</template>
