<script setup lang="ts">
import { ChevronDown, LogOut, Menu, X } from 'lucide-vue-next'
import AppShell from '~/components/AppShell.vue'
import { ADMIN_NAV_GROUPS } from '~/utils/lexicon'

// 管理看板采用左侧 4 组轻量分组栏（桌面端 sticky，移动端折叠抽屉），站点外壳与主题/壁纸与公开页共用。
const route = useRoute()
const { token, logout } = useAdminApi()
const { user } = useAuth()
const mobileOpen = ref(false)

const showingLogin = computed(() => route.path === '/admin/login')
const activeLabel = computed(() => {
  for (const group of ADMIN_NAV_GROUPS) {
    const found = group.items.find(item => item.to === route.path)
    if (found) return found.label
  }
  return '管理看板'
})

watch(() => route.path, () => { mobileOpen.value = false })

function handleLogout() {
  logout()
  navigateTo('/admin/login', { replace: true })
}
</script>

<template>
  <AppShell>
    <main class="min-w-0 py-6 lg:py-8">
      <template v-if="showingLogin">
        <slot />
      </template>

      <div v-else class="space-y-4 lg:grid lg:grid-cols-[204px_minmax(0,1fr)] lg:items-start lg:gap-6 lg:space-y-0">
        <!-- 移动端顶部栏与可折叠分组菜单 -->
        <div class="wall-surface lg:hidden">
          <div class="flex items-center justify-between gap-2">
            <button
              type="button"
              class="inline-flex h-9 items-center gap-2 rounded-[10px] px-2 text-sm font-semibold text-ink hover:bg-chip"
              :aria-expanded="mobileOpen"
              aria-controls="admin-mobile-nav"
              @click="mobileOpen = !mobileOpen"
            >
              <X v-if="mobileOpen" :size="16" />
              <Menu v-else :size="16" />
              <span>管理看板</span>
              <span class="text-faint">/</span>
              <span class="text-brand-ink">{{ activeLabel }}</span>
              <ChevronDown :size="14" class="text-muted transition-transform" :class="{ 'rotate-180': mobileOpen }" />
            </button>
            <div class="flex items-center gap-2 text-xs">
              <NuxtLink to="/" class="text-muted hover:text-ink">返回站点</NuxtLink>
              <button
                v-if="token"
                type="button"
                class="h-8 rounded-lg border border-line px-2.5 text-ink-2 hover:bg-chip"
                @click="handleLogout"
              >
                退出
              </button>
            </div>
          </div>

          <nav v-if="mobileOpen" id="admin-mobile-nav" aria-label="管理功能" class="mt-3 space-y-3 border-t border-line pt-3">
            <div v-for="group in ADMIN_NAV_GROUPS" :key="group.key">
              <p class="mb-1.5 px-1 text-[11px] font-semibold text-faint">{{ group.label }}</p>
              <div class="flex flex-wrap gap-1">
                <NuxtLink
                  v-for="item in group.items"
                  :key="item.to"
                  :to="item.to"
                  class="inline-flex h-8 items-center whitespace-nowrap rounded-lg px-3 text-[13px] transition-colors"
                  :aria-current="route.path === item.to ? 'page' : undefined"
                  :class="route.path === item.to
                    ? 'bg-brand-soft font-semibold text-brand-ink'
                    : 'text-ink-2 hover:bg-chip hover:text-ink'"
                >
                  {{ item.label }}
                </NuxtLink>
              </div>
            </div>
          </nav>
        </div>

        <!-- 桌面端左侧轻量分组栏 -->
        <aside class="panel hidden p-3 lg:sticky lg:top-[84px] lg:block" aria-label="管理功能">
          <div class="border-b border-line-soft px-2 pb-2.5">
            <NuxtLink to="/admin" class="block text-sm font-bold text-ink">管理看板</NuxtLink>
            <p v-if="!token && user?.is_admin" class="mt-0.5 truncate text-[11px] text-faint" :title="user.email">
              {{ user.email }}
            </p>
          </div>

          <nav class="mt-3 space-y-3.5">
            <div v-for="group in ADMIN_NAV_GROUPS" :key="group.key">
              <p class="mb-1 px-2 text-[11px] font-semibold tracking-wide text-faint">{{ group.label }}</p>
              <ul class="space-y-0.5">
                <li v-for="item in group.items" :key="item.to">
                  <NuxtLink
                    :to="item.to"
                    class="flex h-8 items-center rounded-lg px-2.5 text-[13px] transition-colors"
                    :aria-current="route.path === item.to ? 'page' : undefined"
                    :class="route.path === item.to
                      ? 'bg-brand-soft font-semibold text-brand-ink'
                      : 'text-ink-2 hover:bg-chip hover:text-ink'"
                  >
                    {{ item.label }}
                  </NuxtLink>
                </li>
              </ul>
            </div>
          </nav>

          <div class="mt-4 flex items-center justify-between gap-2 border-t border-line-soft px-2 pt-3 text-xs">
            <NuxtLink to="/" class="text-muted transition-colors hover:text-ink">返回站点</NuxtLink>
            <button
              v-if="token"
              type="button"
              class="inline-flex items-center gap-1 text-muted transition-colors hover:text-hot"
              @click="handleLogout"
            >
              <LogOut :size="12" /> 退出管理
            </button>
          </div>
        </aside>

        <div class="min-w-0">
          <slot />
        </div>
      </div>
    </main>
    <AdminToasts />
  </AppShell>
</template>
