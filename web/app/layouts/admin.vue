<script setup lang="ts">
// 管理看板布局：与公开站点共用材质约定（panel / aurora / 暗色），但结构独立
const route = useRoute()
const { token, logout } = useAdminApi()

function handleLogout() {
  logout()
  navigateTo('/admin/login', { replace: true })
}

const nav = [
  { to: '/admin', label: '总览' },
  { to: '/admin/targets', label: '采集目标' },
  { to: '/admin/contents', label: '内容浏览' },
]

const showingLogin = computed(() => route.path === '/admin/login')
</script>

<template>
  <div class="aurora-bg min-h-screen">
    <header class="glass sticky top-0 z-20">
      <div class="mx-auto flex h-14 max-w-5xl items-center gap-6 px-6">
        <NuxtLink to="/admin" class="flex items-center gap-2">
          <span class="text-base font-semibold tracking-tight">AniSignal</span>
          <span class="chip bg-blue-600/10 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300">管理看板</span>
        </NuxtLink>
        <nav v-if="!showingLogin" class="flex items-center gap-1 text-sm">
          <NuxtLink
            v-for="item in nav"
            :key="item.to"
            :to="item.to"
            class="rounded-md px-3 py-1.5 transition-colors"
            :class="route.path === item.to
              ? 'bg-slate-900/5 font-medium text-blue-700 dark:bg-white/10 dark:text-blue-300'
              : 'text-slate-600 hover:bg-slate-900/5 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-zinc-100'"
          >
            {{ item.label }}
          </NuxtLink>
        </nav>
        <div class="ml-auto flex items-center gap-3 text-sm">
          <NuxtLink to="/" class="hidden text-slate-500 hover:text-slate-900 sm:block dark:text-zinc-400 dark:hover:text-zinc-100">返回站点</NuxtLink>
          <button
            v-if="token"
            class="rounded-md border border-slate-900/10 px-3 py-1.5 text-slate-600 transition-colors hover:bg-slate-900/5 dark:border-white/15 dark:text-zinc-300 dark:hover:bg-white/10"
            @click="handleLogout"
          >
            退出
          </button>
        </div>
      </div>
    </header>
    <main class="mx-auto max-w-5xl px-6 py-8">
      <slot />
    </main>
  </div>
</template>
