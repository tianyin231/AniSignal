<script setup lang="ts">
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
  center.loadAll()
  let feedLoaded = false
  watch(tab, (value) => {
    if (value === 'feed' && !feedLoaded) {
      feedLoaded = true
      center.fetchFeed()
    }
  }, { immediate: true })
  // 旧锚点链接：等内容渲染出来再滚到对应卡片（如 #bot-access）
  let scrolled = !route.hash
  watch(user, (value) => {
    if (scrolled || !value) return
    scrolled = true
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
    <div v-else-if="tab === 'notify'" class="grid items-start gap-5 lg:grid-cols-2">
      <AccountEmail @add-keyword="focusKeywords" />
      <div class="space-y-5">
        <AccountBotAccess />
        <RssSubscribe compact />
      </div>
    </div>
    <AccountMonitors v-else-if="tab === 'custom'" />
    <AccountProfile v-else />
  </div>
  <p v-else role="status" class="py-24 text-center text-sm text-faint">正在确认登录状态…</p>
</template>
