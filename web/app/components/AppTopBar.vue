<script setup lang="ts">
import { avatarGradient, displayName } from '~/utils/avatars'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'

const route = useRoute()
// 顶栏只有一层：品牌旁的"频道"组（首页/鸣潮/碧蓝航线…）+ 右侧工具；
// 栏目导航（官方动态/活动日历/商品/事件/内容库）由 SectionNav 放在内容区首行。
const channels = [
  { key: 'home', label: '首页', nav: communityClasses('blue').nav },
  ...GAME_COMMUNITIES.map(c => ({
    key: c.key,
    label: c.key === 'ai' ? 'AI 快讯' : c.label,
    nav: communityClasses(c.color).nav,
    to: c.key === 'ai' ? '/ai' : undefined,
  })),
  // 定制化频道固定最后：用户自建监控（UID/关键词）的公开目录
  { key: 'custom', label: '定制化', nav: communityClasses('blue').nav, to: '/custom' },
]

const { rememberRouteCommunity } = useGameSection()
onMounted(rememberRouteCommunity)
watch(() => [route.path, route.query.community], rememberRouteCommunity)

const { active: activeChannel } = useActiveChannel()
const channelNav = ref<HTMLElement | null>(null)
let navObserver: ResizeObserver | undefined
function revealActiveChannel() {
  const nav = channelNav.value
  const active = nav?.querySelector<HTMLElement>('[aria-current="page"]')
  if (!nav || !active) return
  const bounds = nav.getBoundingClientRect()
  const item = active.getBoundingClientRect()
  // 只滚动频道条，避免切换频道时连带滚动整个页面。
  if (item.left < bounds.left || item.right > bounds.right) {
    nav.scrollTo({ left: nav.scrollLeft + item.left - bounds.left - (bounds.width - item.width) / 2 })
  }
}
watch(activeChannel, () => nextTick(revealActiveChannel))
onMounted(() => {
  revealActiveChannel()
  navObserver = new ResizeObserver(revealActiveChannel)
  if (channelNav.value) navObserver.observe(channelNav.value)
})
onBeforeUnmount(() => navObserver?.disconnect())

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
    <!-- 手机端两行：品牌 + 工具一行，频道独占一行横滑；md 起合并为一行 -->
    <div class="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-3 px-4 md:h-16 md:flex-nowrap lg:gap-6 lg:px-8">
      <NuxtLink to="/" class="flex h-14 shrink-0 items-center gap-2.5 md:h-auto" aria-label="AniSignal 首页">
        <img src="/icon.png" alt="" class="size-8 shrink-0 rounded-[9px]">
        <span class="num text-[19px] font-bold tracking-tight text-ink">AniSignal</span>
      </NuxtLink>
      <nav
        ref="channelNav"
        aria-label="频道"
        class="order-last -mx-4 flex h-11 w-[calc(100%+2rem)] min-w-0 items-stretch overflow-x-auto border-t border-line px-1 [scrollbar-width:none] md:order-none md:mx-0 md:h-auto md:w-auto md:flex-1 md:self-stretch md:border-t-0 md:px-0"
      >
        <NuxtLink
          v-for="ch in channels"
          :key="ch.key"
          :to="ch.to ?? (ch.key === 'home' ? '/' : { path: '/', query: { community: ch.key } })"
          class="flex items-center whitespace-nowrap border-b-2 px-3 text-sm transition-colors"
          :class="activeChannel === ch.key ? `${ch.nav} font-semibold` : 'border-transparent text-muted hover:text-ink'"
          :aria-current="activeChannel === ch.key ? 'page' : undefined"
        >
          {{ ch.label }}
        </NuxtLink>
      </nav>
      <div class="ml-auto flex shrink-0 items-center gap-1 md:ml-0">
        <SearchBox />
        <WallpaperToggle />
        <ThemeToggle />
        <NuxtLink
          v-if="!user"
          to="/login"
          class="btn-primary ml-1 hidden h-9 items-center px-4 sm:inline-flex"
        >
          登录
        </NuxtLink>
        <div v-else ref="menuRoot" class="relative">
          <button
            class="flex h-10 items-center gap-2 rounded-[10px] px-2 text-sm transition-colors hover:bg-chip"
            :aria-expanded="menuOpen"
            aria-haspopup="menu"
            @click="menuOpen = !menuOpen"
          >
            <span class="grid size-7 place-items-center rounded-full bg-gradient-to-b text-xs font-semibold text-white" :class="avatarGradient(user.avatar)">
              {{ displayName(user)[0]!.toUpperCase() }}
            </span>
            <span class="hidden max-w-32 truncate text-[13px] text-ink-2 sm:block">{{ displayName(user) }}</span>
          </button>
          <div v-if="menuOpen" class="popover absolute right-0 top-full z-50 mt-1.5 w-44 p-1.5" role="menu">
            <NuxtLink to="/account" class="block rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-chip" role="menuitem" @click="menuOpen = false">
              账号中心
            </NuxtLink>
            <NuxtLink to="/watch" class="block rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-chip" role="menuitem" @click="menuOpen = false">
              订阅中心
            </NuxtLink>
            <button class="block w-full rounded-lg px-3 py-2 text-left text-sm text-red-600 hover:bg-red-500/10 dark:text-red-400" role="menuitem" @click="handleLogout">
              退出登录
            </button>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>
