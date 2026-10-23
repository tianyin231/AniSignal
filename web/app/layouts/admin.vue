<script setup lang="ts">
import AppShell from '~/components/AppShell.vue'

// 管理功能使用独立导航，站点外壳、主题和壁纸与公开页面共用。
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
  <AppShell>
    <main class="min-w-0 py-6 lg:py-8">
      <div class="wall-surface mb-5 space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <NuxtLink to="/admin" class="text-sm font-bold text-ink">管理看板</NuxtLink>
          <div class="flex items-center gap-3 text-sm">
            <NuxtLink to="/" class="text-muted transition-colors hover:text-ink">返回站点</NuxtLink>
            <button
              v-if="token"
              class="h-9 rounded-[10px] border border-line px-3.5 text-ink-2 transition-colors hover:bg-chip"
              @click="handleLogout"
            >
              退出管理
            </button>
          </div>
        </div>
        <nav v-if="!showingLogin" aria-label="管理功能" class="flex flex-wrap gap-1 border-t border-line pt-3 text-sm">
          <NuxtLink
            v-for="item in nav"
            :key="item.to"
            :to="item.to"
            class="inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full px-3.5 transition-colors"
            :aria-current="route.path === item.to ? 'page' : undefined"
            :class="route.path === item.to
              ? 'bg-ink font-semibold text-card'
              : 'text-ink-2 hover:bg-chip hover:text-ink'"
          >
            {{ item.label }}
          </NuxtLink>
        </nav>
      </div>
      <slot />
    </main>
    <AdminToasts />
  </AppShell>
</template>
