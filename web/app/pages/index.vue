<script setup lang="ts">
import { events, morningReport, pastReports } from '~/utils/data'

const openReport = ref(-1)
const tabs = [
  { key: 'latest', label: '最新' },
  { key: 'important', label: '重要事件' },
  { key: 'report', label: '今日早报' },
  { key: 'mine', label: '我订阅的' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'latest'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})

const latest = [...events].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
const list = computed(() => (tab.value === 'important' ? latest.filter((e) => e.important) : latest))

useSeoMeta({ title: '今日情报' })
</script>

<template>
  <div>
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="tab === 'latest' || tab === 'important'" class="panel overflow-hidden">
      <EventRow v-for="e in list" :key="e.id" :event="e" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此类事件。
      </p>
      <div class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>第 1 页 · 共 {{ list.length }} 条</span>
        <span class="mono">« 1 »</span>
      </div>
    </section>

    <section v-else-if="tab === 'report'" class="panel p-4 sm:p-6">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">每日早报</h1>
        <p class="mono text-xs text-slate-400 dark:text-zinc-500">
          {{ morningReport.date }} {{ morningReport.sentAt }} 发送 · 截止 {{ morningReport.cutoff }}
        </p>
      </div>
      <div class="mt-5 space-y-6">
        <div v-for="s in morningReport.sections" :key="s.title">
          <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">{{ s.title }}</h2>
          <ul class="mt-2 space-y-2 border-l border-slate-200 pl-4 dark:border-zinc-800">
            <li
              v-for="(it, i) in s.items"
              :key="i"
              class="relative text-sm leading-relaxed text-slate-600 dark:text-zinc-300"
            >
              <span class="absolute -left-[19px] top-2 size-1.5 rounded-full bg-blue-400" />
              <NuxtLink v-if="it.eventId" :to="`/events/${it.eventId}`" class="link">{{ it.text }}</NuxtLink>
              <span v-else>{{ it.text }}</span>
            </li>
          </ul>
        </div>
      </div>
      <p class="mt-6 border-t border-slate-100 pt-3 text-xs leading-relaxed text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        早报收纳普通更新；重大事件不等待早报，即时发送。无新增时明确说明，而不是静默跳过。
      </p>

      <section class="panel mt-4 p-4">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">往期早报</h2>
        <div class="mt-2 divide-y divide-slate-100 dark:divide-zinc-800/60">
          <div v-for="(r, i) in pastReports" :key="r.date">
            <button
              type="button"
              class="flex w-full items-center justify-between py-2.5"
              @click="openReport = openReport === i ? -1 : i"
            >
              <span class="mono text-sm text-slate-600 dark:text-zinc-300">{{ r.date }}</span>
              <span class="text-xs text-slate-400 dark:text-zinc-500">{{ r.items.length }} 条 · {{ openReport === i ? '收起' : '展开' }}</span>
            </button>
            <ul v-if="openReport === i" class="space-y-1.5 pb-3 pl-1 text-[13px] text-slate-500 dark:text-zinc-400">
              <li v-for="it in r.items" :key="it.text" class="flex gap-2">
                <span class="mt-1.5 size-1 shrink-0 rounded-full bg-slate-300 dark:bg-zinc-600" />
                <NuxtLink v-if="it.eventId" :to="`/events/${it.eventId}`" class="link">{{ it.text }}</NuxtLink>
                <span v-else>{{ it.text }}</span>
              </li>
            </ul>
          </div>
        </div>
      </section>
    </section>

    <section v-else class="space-y-4">
      <div class="panel border-dashed p-4 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
        原型未接入登录（规划：邮箱验证码，无密码）。订阅后这里显示监控位状态、触发记录与早报送达历史。以下为演示数据。
      </div>
      <WatchOverview />
    </section>

    <div class="mt-6 space-y-4 xl:hidden">
      <StatusCard />
      <ReportCard />
    </div>
  </div>
</template>
