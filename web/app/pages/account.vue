<script setup lang="ts">
import { ChevronDown, Code2 } from 'lucide-vue-next'
import { ACCOUNT_CENTER, ACCOUNT_TABS, createAccountCenter, type AccountTab } from '~/composables/useAccountCenter'
import { loginRoute } from '~/utils/safe-redirect'

definePageMeta({ layout: 'account' })
useSeoMeta({ title: '账号中心 · AniSignal' })

const route = useRoute()
const router = useRouter()
const { user, loaded, signingOut } = useAuth()
const center = createAccountCenter()
provide(ACCOUNT_CENTER, center)

const TAB_KEYS = new Set<string>(ACCOUNT_TABS.map(t => t.key))
// 旧版单页的锚点 → 对应标签页（站内旧链接与书签仍然有效）
const LEGACY_HASH: Record<string, AccountTab> = {
  '#keyword-monitor': 'feed', '#email-notifications': 'notify', '#bot-access': 'notify', '#custom-monitors': 'custom',
}
const tab = computed<AccountTab>(() => {
  const value = String(route.query.tab ?? '')
  return TAB_KEYS.has(value) ? value as AccountTab : LEGACY_HASH[route.hash] ?? 'feed'
})
function tabRoute(key: AccountTab) {
  const { tab: _current, ...query } = route.query
  return { path: '/account', query: key === 'feed' ? query : { ...query, tab: key } }
}
const selectTab = (key: AccountTab) => router.replace(tabRoute(key))

const ADVANCED_HASHES = new Set(['#webhooks', '#rss', '#mcp'])
const advancedNotifyOpen = ref(ADVANCED_HASHES.has(route.hash))

const keywordsPanel = ref<{ focus: () => void } | null>(null)
async function focusKeywords() {
  if (tab.value !== 'feed') await selectTab('feed')
  await nextTick()
  document.getElementById('keyword-monitor')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  keywordsPanel.value?.focus()
}
function onPreferencesSaved() {
  center.filterFeed(center.feedKeywordId.value)
  center.fetchOverview()
}

onMounted(() => {
  // 未登录或会话过期：带着当前位置去登录；主动退出登录时不跳
  watch([loaded, user], () => {
    if (loaded.value && !user.value && !signingOut.value) navigateTo(loginRoute(route.fullPath), { replace: true })
  }, { immediate: true })
  // 确认登录后才拉账号数据：未登录时不先发一串注定 401 的请求再跳走
  let started = false
  let feedLoaded = false
  watch(user, (value) => {
    if (!value || started) return
    started = true
    center.loadAll()
    watch(tab, (key) => {
      if (key === 'feed' && !feedLoaded) {
        feedLoaded = true
        center.fetchFeed()
      }
    }, { immediate: true })
  }, { immediate: true })
  // 旧锚点链接：等内容渲染出来再滚到对应卡片（如 #bot-access）
  let scrolled = !route.hash
  watch(user, (value) => {
    if (scrolled || !value) return
    scrolled = true
    if (ADVANCED_HASHES.has(route.hash)) advancedNotifyOpen.value = true
    nextTick(() => document.getElementById(route.hash.slice(1))?.scrollIntoView({ block: 'start' }))
  }, { immediate: true })
})
onBeforeUnmount(center.dispose)
</script>

<template>
  <div v-if="user">
    <AccountHeader @select="selectTab" />

    <nav class="wall-surface mb-5 flex w-fit max-w-full items-center gap-1 overflow-x-auto [scrollbar-width:none]" aria-label="账号中心">
      <NuxtLink
        v-for="t in ACCOUNT_TABS"
        :key="t.key"
        :to="tabRoute(t.key)"
        replace
        class="inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full px-3.5 text-sm transition-colors"
        :class="tab === t.key ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-chip hover:text-ink'"
        :aria-current="tab === t.key ? 'page' : undefined"
      >
        {{ t.label }}
      </NuxtLink>
    </nav>

    <div v-if="tab === 'feed'" class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-[auto_1fr]">
      <AccountKeywords ref="keywordsPanel" class="lg:col-start-2 lg:row-start-1" />
      <AccountFeed class="lg:col-start-1 lg:row-span-2 lg:row-start-1" @add-keyword="focusKeywords" />
      <AccountPreferences class="lg:col-start-2 lg:row-start-2" @saved="onPreferencesSaved" />
    </div>
    <div v-else-if="tab === 'notify'" class="space-y-6">
      <section aria-label="常用通知">
        <div class="grid items-start gap-5 lg:grid-cols-2">
          <AccountEmail @add-keyword="focusKeywords" />
          <AccountBotAccess />
        </div>
      </section>

      <section aria-labelledby="advanced-notify-title" class="space-y-4">
        <button
          type="button"
          class="panel flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left transition-colors hover:border-faint/60"
          :aria-expanded="advancedNotifyOpen"
          aria-controls="advanced-notify-grid"
          @click="advancedNotifyOpen = !advancedNotifyOpen"
        >
          <span class="flex min-w-0 items-center gap-3">
            <span class="grid size-9 shrink-0 place-items-center rounded-xl bg-chip text-ink-2" aria-hidden="true">
              <Code2 :size="17" />
            </span>
            <span class="min-w-0">
              <span id="advanced-notify-title" class="block text-sm font-bold text-ink">开发者与进阶接入</span>
              <span class="mt-0.5 block truncate text-xs text-muted">Webhook 推送 · 个人 RSS 订阅 · MCP 接入</span>
            </span>
          </span>
          <span class="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-muted">
            {{ advancedNotifyOpen ? '收起' : '展开配置' }}
            <ChevronDown :size="15" class="transition-transform" :class="{ 'rotate-180': advancedNotifyOpen }" />
          </span>
        </button>

        <div v-if="advancedNotifyOpen" id="advanced-notify-grid" class="grid items-start gap-5 lg:grid-cols-2">
          <div id="webhooks" class="scroll-mt-24">
            <AccountWebhooks />
          </div>
          <div class="space-y-5">
            <div id="rss" class="scroll-mt-24">
              <RssSubscribe compact personal />
            </div>
            <div id="mcp" class="scroll-mt-24">
              <McpConnect compact personal />
            </div>
          </div>
        </div>
      </section>
    </div>
    <AccountMonitors v-else-if="tab === 'custom'" />
    <div v-else class="space-y-5">
      <AccountProfile />
      <AccountHiddenCommunities />
    </div>
  </div>
  <p v-else role="status" class="py-24 text-center text-sm text-faint">正在确认登录状态…</p>
</template>
