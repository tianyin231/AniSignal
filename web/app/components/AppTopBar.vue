<script setup lang="ts">
import { avatarGradient, displayName } from '~/utils/avatars'
import { loginRoute } from '~/utils/safe-redirect'
import type { RouteLocationRaw } from 'vue-router'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'

const route = useRoute()
// 顶栏只有一层：品牌旁的"频道"组（首页/鸣潮/碧蓝航线…）+ 右侧工具；
// 栏目导航（官方动态/活动日历/商品/事件/内容库）由 SectionNav 放在内容区首行。
const brand = communityClasses('blue')
const channels = [
  { key: 'home', label: '首页', nav: brand.nav, dot: brand.dot, to: '/' as RouteLocationRaw },
  ...GAME_COMMUNITIES.map(c => ({
    key: c.key,
    label: c.key === 'ai' ? 'AI 快讯' : c.label,
    nav: communityClasses(c.color).nav,
    dot: communityClasses(c.color).dot,
    to: (c.key === 'ai' ? '/ai' : { path: '/', query: { community: c.key } }) as RouteLocationRaw,
  })),
  // 定制化频道固定最后：用户自建监控（UID/关键词）的公开目录
  { key: 'custom', label: '定制化', nav: brand.nav, dot: brand.dot, to: '/custom' as RouteLocationRaw },
]

const { rememberRouteCommunity } = useGameSection()
onMounted(rememberRouteCommunity)
watch(() => [route.path, route.query.community], rememberRouteCommunity)

const { active } = useActiveChannel()
// 全站工具页沿用首页主题色，但顶栏高亮的是"更多"而不是"首页"
const TOOL_PATHS = ['/stats', '/sources', '/watch', '/project']
const activeChannel = computed(() => (TOOL_PATHS.includes(route.path) ? null : active.value))

// ---------- 频道按宽度收纳（md 起）----------
// 依次放入能完整显示的频道，其余收进"更多"；当前频道始终占一个位置。手机端整行横滑，不收纳。
const channelBar = ref<HTMLElement | null>(null)
const channelNav = ref<HTMLElement | null>(null)
const moreMenu = ref<{ $el: HTMLElement } | null>(null)
const channelMeasure = ref<HTMLElement | null>(null)
const desktop = ref(false)
const channelWidths = ref<number[]>([])
const trackWidth = ref(0)
const visibleKeys = computed<Set<string> | null>(() => {
  const widths = channelWidths.value
  const room = trackWidth.value
  if (!desktop.value || widths.length !== channels.length || !room) return null
  if (widths.reduce((sum, w) => sum + w, 0) <= room) return null
  const activeIndex = channels.findIndex(ch => ch.key === activeChannel.value)
  const keys = new Set<string>()
  let used = 0
  if (activeIndex >= 0) {
    keys.add(channels[activeIndex]!.key)
    used = widths[activeIndex]!
  }
  for (const [index, ch] of channels.entries()) {
    if (index === activeIndex) continue
    if (used + widths[index]! > room) break
    used += widths[index]!
    keys.add(ch.key)
  }
  return keys
})
const shownChannels = computed(() => (visibleKeys.value ? channels.filter(ch => visibleKeys.value!.has(ch.key)) : channels))
const overflowChannels = computed(() => (visibleKeys.value ? channels.filter(ch => !visibleKeys.value!.has(ch.key)) : []))
function measureChannels() {
  // 隐藏测量行按选中态（semibold）排版取宽度上限，切换频道时不会因字重变化被挤出
  const row = channelMeasure.value
  if (row) channelWidths.value = [...row.children].map(el => Math.ceil(el.getBoundingClientRect().width))
}

let navObserver: ResizeObserver | undefined
let desktopQuery: MediaQueryList | undefined
const syncDesktop = () => { desktop.value = !!desktopQuery?.matches }
function revealActiveChannel() {
  const nav = channelNav.value
  if (nav && desktop.value) {
    // 桌面端靠收纳而不是滚动：保持从头对齐
    nav.scrollLeft = 0
    return
  }
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
  desktopQuery = window.matchMedia('(min-width: 768px)')
  syncDesktop()
  desktopQuery.addEventListener('change', syncDesktop)
  measureChannels()
  document.fonts?.ready.then(measureChannels)
  revealActiveChannel()
  // 可用宽度 = 整条频道栏 − "更多"按钮；频道条本身按内容收缩，"更多"紧跟在最后一个频道后面
  navObserver = new ResizeObserver(() => {
    trackWidth.value = (channelBar.value?.clientWidth ?? 0) - (moreMenu.value?.$el.offsetWidth ?? 0)
    revealActiveChannel()
  })
  if (channelBar.value) navObserver.observe(channelBar.value)
  if (channelNav.value) navObserver.observe(channelNav.value)
})
onBeforeUnmount(() => {
  navObserver?.disconnect()
  desktopQuery?.removeEventListener('change', syncDesktop)
})

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
        <img src="/icon-64.webp" alt="" width="32" height="32" class="size-8 shrink-0 rounded-[9px]">
        <span class="num text-[19px] font-bold tracking-tight text-ink">AniSignal</span>
      </NuxtLink>
      <nav
        ref="channelBar"
        aria-label="频道"
        class="order-last -mx-4 flex h-11 w-[calc(100%+2rem)] min-w-0 items-stretch border-t border-line pl-1 md:order-none md:mx-0 md:h-auto md:w-auto md:flex-1 md:self-stretch md:border-t-0 md:pl-0"
      >
        <!-- 手机端频道条横滑、右缘渐隐提示还能滑；md 起放不下的频道收进"更多" -->
        <div
          ref="channelNav"
          class="flex min-w-0 flex-1 items-stretch overflow-x-auto md:flex-initial [mask-image:linear-gradient(to_right,#000_calc(100%-28px),transparent)] [scrollbar-width:none] md:overflow-hidden md:[mask-image:none]"
        >
          <NuxtLink
            v-for="ch in shownChannels"
            :key="ch.key"
            :to="ch.to"
            class="flex shrink-0 items-center whitespace-nowrap border-b-2 px-3 text-sm transition-colors"
            :class="activeChannel === ch.key ? `${ch.nav} font-semibold` : 'border-transparent text-muted hover:text-ink'"
            :aria-current="activeChannel === ch.key ? 'page' : undefined"
          >
            {{ ch.label }}
          </NuxtLink>
        </div>
        <TopBarMore ref="moreMenu" :channels="overflowChannels" />
        <div class="pointer-events-none absolute size-0 overflow-hidden" aria-hidden="true">
          <div ref="channelMeasure" class="invisible flex w-max">
            <span v-for="ch in channels" :key="ch.key" class="shrink-0 whitespace-nowrap border-b-2 px-3 text-sm font-semibold">{{ ch.label }}</span>
          </div>
        </div>
      </nav>
      <div class="ml-auto flex shrink-0 items-center gap-1 md:ml-0">
        <SearchBox />
        <WallpaperToggle />
        <ThemeToggle />
        <NuxtLink
          v-if="!user"
          :to="loginRoute(route.fullPath)"
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
            <span class="hidden max-w-32 truncate text-[13px] text-ink-2 sm:block md:hidden 2xl:block">{{ displayName(user) }}</span>
          </button>
          <div v-if="menuOpen" class="popover absolute right-0 top-full z-50 mt-1.5 w-56 p-1.5" role="menu">
            <div class="mb-1 border-b border-line-soft px-3 pb-2.5 pt-2">
              <p class="truncate text-sm font-semibold text-ink">{{ displayName(user) }}</p>
              <p class="truncate text-xs text-muted">{{ user.email }}</p>
            </div>
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
