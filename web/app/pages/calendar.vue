<script setup lang="ts">
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-vue-next'
import { GAME_COMMUNITIES, communityClasses } from '~/composables/useGameSection'
import type { CalendarEntry } from '~/composables/usePublicApi'
import { beijingDay, calendarDays, shiftCalendarMonth } from '~/utils/calendar'
import { fmtTime } from '~/utils/admin-types'

useSeoMeta({ title: '活动日历 · AniSignal' })

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()
const today = useState('calendar-today', () => beijingDay(Date.now()))
const month = computed(() => /^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$/.test(String(route.query.month ?? ''))
  ? String(route.query.month) : today.value.slice(0, 7))
const monthLabel = computed(() => `${Number(month.value.slice(0, 4))} 年 ${Number(month.value.slice(5))} 月`)
const selectedDay = ref(month.value === today.value.slice(0, 7) ? today.value : `${month.value}-01`)
watch(month, value => { selectedDay.value = value === today.value.slice(0, 7) ? today.value : `${value}-01` })

const community = computed({
  get: () => GAME_COMMUNITIES.some(c => c.key === route.query.community) ? String(route.query.community) : 'all',
  set: (value: string) => router.replace({ query: { ...route.query, community: value } }),
})
const { data, status, error, refresh } = await useAsyncData(
  () => `calendar-month-${community.value}-${month.value}`,
  () => api<{ month: string, items: CalendarEntry[] }>('/api/calendar/month', { query: { community: community.value, month: month.value } }),
  { watch: [community, month] },
)
const days = computed(() => calendarDays(month.value, status.value === 'success' ? data.value?.items ?? [] : []))
const weeks = computed(() => Array.from({ length: 6 }, (_, index) => days.value.slice(index * 7, index * 7 + 7)))
const selectedItems = computed(() => days.value.find(day => day.date === selectedDay.value)?.items ?? [])
const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const selectedLabel = computed(() => new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' }).format(new Date(`${selectedDay.value}T00:00:00Z`)))
const kindLabels: Record<string, string> = { activity: '活动', maintenance: '更新 / 维护', pool: '卡池', broadcast: '直播', delay: '变更', deadline: '截止' }
const communityMeta = (key?: string) => GAME_COMMUNITIES.find(c => c.key === key)
const itemColor = (item: CalendarEntry) => communityClasses(communityMeta(item.community)?.color ?? 'blue').chip

function changeMonth(offset: number) {
  router.replace({ query: { ...route.query, month: shiftCalendarMonth(month.value, offset) } })
}
function goToToday() {
  selectedDay.value = today.value
  router.replace({ query: { ...route.query, month: today.value.slice(0, 7) } })
}
function timeRange(item: CalendarEntry) {
  const format = (value: string | null) => item.all_day ? fmtTime(value).slice(0, 10) : fmtTime(value)
  if (item.kind === 'deadline') return `${format(item.start_at)} 截止`
  if (item.end_at) return `${format(item.start_at)} — ${format(item.end_at)}`
  return `${format(item.start_at)} 开始 · 结束时间未公布`
}
</script>

<template>
  <div>
    <header class="mb-5 flex flex-wrap items-center justify-between gap-3 px-1">
      <div>
        <h1 class="text-xl font-semibold text-slate-900 dark:text-zinc-50">活动日历</h1>
        <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">北京时间 · 点选日期查看日程</p>
      </div>
      <div class="flex items-center gap-2">
        <select v-model="community" aria-label="游戏范围" class="select min-h-11 min-w-0 px-2 py-1.5 text-sm">
          <option value="all">全部游戏</option>
          <option v-for="c in GAME_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
        <button type="button" class="btn-ghost grid size-11 place-items-center" aria-label="刷新日历" :disabled="status === 'pending'" @click="refresh()">
          <RefreshCw :size="16" :class="{ 'animate-spin': status === 'pending' }" />
        </button>
      </div>
    </header>

    <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
      <section class="panel min-w-0 overflow-hidden" :aria-label="`${monthLabel}月历`" :aria-busy="status === 'pending'">
        <div class="flex items-center justify-between gap-2 px-3 py-3 sm:px-4">
          <h2 class="text-lg font-semibold tabular-nums text-slate-900 dark:text-zinc-50">{{ monthLabel }}</h2>
          <nav aria-label="月份切换" class="flex shrink-0 items-center gap-0.5">
            <button type="button" class="btn-ghost grid size-11 place-items-center" aria-label="上个月" :disabled="month === '1900-01'" @click="changeMonth(-1)"><ChevronLeft :size="18" /></button>
            <button type="button" class="btn-ghost min-h-11 px-2 text-sm" @click="goToToday">今天</button>
            <button type="button" class="btn-ghost grid size-11 place-items-center" aria-label="下个月" :disabled="month === '2199-12'" @click="changeMonth(1)"><ChevronRight :size="18" /></button>
          </nav>
        </div>
        <p v-if="status === 'pending'" role="status" class="border-t border-slate-900/5 px-4 py-2 text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">正在加载日程…</p>
        <p v-else-if="error" role="alert" class="border-t border-slate-900/5 px-4 py-2 text-sm text-slate-600 dark:border-white/10 dark:text-zinc-300">
          日程加载失败，<button type="button" class="link" @click="refresh()">重新加载</button>
        </p>
        <table class="w-full table-fixed border-collapse" :aria-label="`${monthLabel}日期`">
          <thead>
            <tr class="border-y border-slate-900/5 dark:border-white/10">
              <th v-for="(day, index) in weekdays" :key="day" scope="col" class="py-2.5 text-center text-xs font-medium" :class="index > 4 ? 'text-slate-400 dark:text-zinc-500' : 'text-slate-600 dark:text-zinc-300'">{{ day }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(week, index) in weeks" :key="index">
              <td v-for="day in week" :key="day.date" class="border-b border-r border-slate-900/5 p-0 align-top last:border-r-0 dark:border-white/10">
                <button
                  type="button"
                  class="relative flex h-[72px] w-full min-w-0 flex-col text-left transition-colors hover:bg-blue-500/5 focus-visible:z-10 focus-visible:outline-offset-[-2px] sm:h-28"
                  :class="selectedDay === day.date ? 'bg-blue-500/10 ring-2 ring-inset ring-blue-500 dark:bg-blue-400/10' : !day.inMonth ? 'bg-slate-500/5 text-slate-400 dark:text-zinc-500' : 'text-slate-700 dark:text-zinc-200'"
                  :aria-label="`${day.date}，${day.items.length} 项日程${day.date === today ? '，今天' : ''}`"
                  :aria-pressed="selectedDay === day.date"
                  :aria-current="day.date === today ? 'date' : undefined"
                  @click="selectedDay = day.date"
                >
                  <span class="mx-1 mt-1 grid size-7 shrink-0 place-items-center rounded-full text-sm tabular-nums sm:mx-1.5 sm:mt-1.5" :class="day.date === today ? 'bg-blue-600 font-semibold text-white' : ''">{{ day.number }}</span>
                  <span class="mt-1 hidden w-full min-w-0 space-y-1 px-1 sm:block">
                    <span v-for="item in day.items.slice(0, 2)" :key="item.id" class="block truncate rounded px-1.5 py-0.5 text-[11px] leading-4" :class="itemColor(item)" :title="`${communityMeta(item.community)?.label ?? ''} · ${item.title}`">{{ item.title }}</span>
                    <span v-if="day.items.length > 2" class="block px-1 text-[10px] text-slate-500 dark:text-zinc-400">另 {{ day.items.length - 2 }} 项</span>
                  </span>
                  <span v-if="day.items.length" class="mt-1 flex w-full flex-col items-center gap-1 sm:hidden">
                    <span class="flex gap-0.5" aria-hidden="true"><span v-for="item in day.items.slice(0, 3)" :key="item.id" class="rounded-full p-0.5" :class="itemColor(item)"><span class="block size-1 rounded-full bg-current" /></span></span>
                    <span class="text-[10px] text-slate-500 dark:text-zinc-400">{{ day.items.length }} 项</span>
                  </span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="community === 'all'" class="flex flex-wrap gap-x-3 gap-y-2 px-3 py-3 text-[11px] text-slate-600 dark:text-zinc-300">
          <span v-for="c in GAME_COMMUNITIES" :key="c.key" class="flex items-center gap-1.5"><span class="rounded-full p-1" :class="communityClasses(c.color).chip"><span class="block size-1.5 rounded-full bg-current" /></span>{{ c.label }}</span>
        </div>
      </section>

      <section class="panel min-w-0 overflow-hidden xl:sticky xl:top-[72px]" aria-labelledby="selected-date-title" aria-live="polite">
        <div class="border-b border-slate-900/5 px-4 py-4 dark:border-white/10">
          <h2 id="selected-date-title" class="text-base font-semibold text-slate-900 dark:text-zinc-50">{{ selectedLabel }}</h2>
          <p v-if="status === 'success'" class="mt-1 text-xs text-slate-500 dark:text-zinc-400">{{ selectedItems.length }} 项日程</p>
        </div>
        <p v-if="status === 'pending'" class="px-4 py-6 text-sm text-slate-500 dark:text-zinc-400">正在加载…</p>
        <p v-else-if="error" class="px-4 py-6 text-sm text-slate-500 dark:text-zinc-400">日程暂时无法显示。</p>
        <div v-else class="divide-y divide-slate-900/5 dark:divide-white/10">
          <article v-for="item in selectedItems" :key="item.id" class="px-4 py-4">
            <div class="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-zinc-400">
              <span v-if="communityMeta(item.community)" class="rounded px-1.5 py-0.5" :class="itemColor(item)">{{ communityMeta(item.community)!.label }}</span>
              <span>{{ kindLabels[item.kind] ?? item.kind }}</span>
              <span v-if="item.delayed">时间已调整</span>
            </div>
            <NuxtLink :to="`/contents/${item.content_id}`" class="break-words text-sm font-medium leading-relaxed text-slate-800 hover:text-blue-700 dark:text-zinc-100 dark:hover:text-blue-300">{{ item.title }}</NuxtLink>
            <p class="mt-2 text-xs leading-relaxed tabular-nums text-slate-500 dark:text-zinc-400">{{ timeRange(item) }}</p>
            <div class="mt-1 flex flex-wrap gap-x-4 text-xs">
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link flex min-h-11 items-center">原文</a>
              <a :href="`/api/calendar/${item.id}.ics`" class="link flex min-h-11 items-center" :aria-label="`将${item.title}加入日历`">加入日历</a>
            </div>
          </article>
          <p v-if="!selectedItems.length" class="px-4 py-8 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">这一天暂无收录的日程。</p>
        </div>
      </section>
    </div>
  </div>
</template>
