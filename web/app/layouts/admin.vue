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
  { to: '/admin/users', label: '用户管理' },
  { to: '/admin/bot', label: '机器人通知' },
  { to: '/admin/customs', label: '定制监控' },
  { to: '/admin/targets', label: '采集目标' },
  { to: '/admin/proxies', label: '采集代理' },
  { to: '/admin/events', label: '事件审核' },
  { to: '/admin/products', label: '商品管理' },
  { to: '/admin/rules', label: '分类词典' },
  { to: '/admin/ai', label: 'AI 层' },
  { to: '/admin/aihot', label: 'AI 新闻引擎' },
  { to: '/admin/settings', label: '站点设置' },
  { to: '/admin/contents', label: '内容管理' },
]

const showingLogin = computed(() => route.path === '/admin/login')
</script>

<template>
  <div class="aurora-bg min-h-screen">
    <header class="glass sticky top-0 z-20">
      <div class="mx-auto max-w-5xl px-4 sm:px-6">
        <div class="flex h-14 items-center gap-4">
          <NuxtLink to="/admin" class="flex shrink-0 items-center gap-2">
            <img src="/icon.png" alt="" class="size-7 shrink-0 rounded-lg">
            <span class="num text-[17px] font-bold tracking-tight text-ink">AniSignal</span>
            <span class="rounded-md bg-brand-soft px-2 py-0.5 text-xs font-semibold text-brand-ink">管理看板</span>
          </NuxtLink>
          <div class="ml-auto flex shrink-0 items-center gap-3 text-sm">
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
        <nav v-if="!showingLogin" aria-label="管理功能" class="flex flex-wrap gap-1 border-t border-slate-900/5 py-2 text-sm dark:border-white/10">
          <NuxtLink
            v-for="item in nav"
            :key="item.to"
            :to="item.to"
            class="shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 transition-colors"
            :aria-current="route.path === item.to ? 'page' : undefined"
            :class="route.path === item.to
              ? 'bg-ink font-semibold text-card'
              : 'text-ink-2 hover:bg-chip hover:text-ink'"
          >
            {{ item.label }}
          </NuxtLink>
        </nav>
      </div>
    </header>
    <main class="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <slot />
    </main>
    <AdminToasts />
  </div>
</template>
