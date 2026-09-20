<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import { GAME_COMMUNITIES } from '~/composables/useGameSection'
import type { CalendarDto } from '~/composables/usePublicApi'

useSeoMeta({ title: '活动日历 · AniSignal' })

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()
// 直接访问日历展示全部游戏；从分区进入时由链接显式保留范围。
const community = computed({
  get: () => GAME_COMMUNITIES.some(c => c.key === route.query.community) ? String(route.query.community) : 'all',
  set: (value: string) => router.replace({ query: { community: value } }),
})
const { data, status, error, refresh } = await useAsyncData(
  () => `calendar-page-${community.value}`,
  () => api<CalendarDto>('/api/calendar', { query: { community: community.value } }),
  { watch: [community] },
)
const hasEntries = computed(() => data.value && Object.values(data.value).some(items => items.length))
</script>

<template>
  <div>
    <header class="panel mb-4 p-4 sm:p-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">活动日历</h1>
        <button type="button" class="btn-ghost flex items-center gap-1.5 px-2 py-1 text-xs" :disabled="status === 'pending'" @click="refresh()">
          <RefreshCw :size="13" :class="{ 'animate-spin': status === 'pending' }" />
          刷新
        </button>
      </div>
      <p class="mt-2 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">官方公告中的近期活动、卡池、直播与更新安排。时间均为北京时间。</p>
      <label class="mt-4 flex flex-wrap items-center gap-2 text-sm text-slate-600 dark:text-zinc-300">
        游戏范围
        <select v-model="community" class="select min-w-0 px-2 py-1.5 text-sm">
          <option value="all">全部游戏</option>
          <option v-for="c in GAME_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
      </label>
    </header>

    <p v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-500 dark:text-zinc-400" role="status">正在加载日程…</p>
    <div v-else-if="error" class="panel px-4 py-10 text-center text-sm" role="alert">
      <p class="text-slate-500 dark:text-zinc-400">日程暂时加载失败，请稍后重试。</p>
      <button type="button" class="link mt-3" @click="refresh()">重新加载</button>
    </div>
    <ActivityCalendar v-else-if="hasEntries" :data="data" :show-community="community === 'all'" :show-title="false" show-ended />
    <p v-else class="panel px-4 py-10 text-center text-sm text-slate-500 dark:text-zinc-400">当前范围暂无日程。</p>
  </div>
</template>
