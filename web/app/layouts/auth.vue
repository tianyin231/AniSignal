<script setup lang="ts">
import type { ApiEventList, PublicStats } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { categoryMeta, toAppEvent } from '~/utils/public-utils'

// 认证专属布局：极简品牌头 + 居中表单 + 站点内容预览（真实数据）
const { api } = usePublicApi()
const { data: stats } = await useAsyncData('auth-stats', () => api<PublicStats>('/api/stats'))
const { data: eventsData } = await useAsyncData('auth-events', () =>
  api<ApiEventList>('/api/events', { query: { limit: 3 } }),
)
const events = computed(() => (eventsData.value?.items ?? []).map(toAppEvent))
</script>

<template>
  <div class="aurora-bg min-h-screen">
    <WallpaperBackdrop />
  <header class="glass sticky top-0 z-20">
      <div class="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
        <NuxtLink to="/" class="flex items-center gap-2">
          <img src="/icon.png" alt="" class="size-7 shrink-0">
          <span class="text-[15px] font-semibold tracking-tight">AniSignal</span>
          <span class="chip ml-1 bg-blue-600/10 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300">游戏事件雷达</span>
        </NuxtLink>
        <NuxtLink to="/" class="text-sm text-slate-500 transition-colors hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-100">
          直接浏览 →
        </NuxtLink>
      </div>
    </header>
    <AnnouncementBanner />

    <main class="mx-auto grid max-w-5xl items-center gap-8 px-6 py-10 lg:grid-cols-[minmax(0,400px)_minmax(0,1fr)] lg:py-20">
      <div class="w-full max-w-sm justify-self-center lg:justify-self-start">
        <slot />
      </div>

      <!-- 站点内容预览：不用登录就能看到的价值 -->
      <section class="panel-glass hidden space-y-4 p-5 lg:block">
        <div>
          <h2 class="text-sm font-semibold">正在发生的事</h2>
        </div>
        <ul class="space-y-2.5">
          <li v-for="e in events" :key="e.id">
            <NuxtLink :to="`/events/${e.id}`" class="group block rounded-lg border border-slate-900/5 p-3 transition-colors hover:bg-slate-500/5 dark:border-white/10">
              <div class="flex items-center gap-2 text-xs">
                <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ e.category }}</span>
                <span v-if="e.important" class="chip bg-red-500/10 text-red-600 dark:text-red-400">重要</span>
                <span class="mono ml-auto text-slate-400">{{ e.updatedAt }}</span>
              </div>
              <p class="mt-1.5 truncate text-sm font-medium transition-colors group-hover:text-blue-600 dark:group-hover:text-blue-300">
                {{ e.title }}
              </p>
              <p class="mono mt-1 text-xs text-slate-400">证据 {{ e.evidenceCount }} 条 · 可信度 {{ e.confidence }}</p>
            </NuxtLink>
          </li>
          <li v-if="!events.length" class="py-4 text-center text-sm text-slate-400">事件整理中…</li>
        </ul>
        <p v-if="stats" class="mono border-t border-slate-900/5 pt-3 text-xs text-slate-400 dark:border-white/10">
          已入库 {{ stats.total_contents }} 条内容 · {{ stats.official_contents }} 条官方 · 最近采集 {{ fmtTime(stats.latest_collected_at) }}
        </p>
      </section>
    </main>

    <footer class="mx-auto max-w-5xl px-6 pb-8 text-xs text-slate-400 dark:text-zinc-500">
      <NuxtLink to="/about" class="hover:text-slate-600 dark:hover:text-zinc-300">关于 AniSignal 与数据边界</NuxtLink>
    </footer>
  </div>
</template>
