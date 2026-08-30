<script setup lang="ts">
import { ExternalLink } from 'lucide-vue-next'
import { fmtTime } from '~/utils/admin-types'

// 官方动态外链直达：variant="list" 用于桌面侧栏，variant="strip" 用于移动端横滑条（lg 以下显示）
const props = withDefaults(defineProps<{ variant?: 'list' | 'strip' }>(), { variant: 'list' })

const { items, fetchOnce } = useOfficialLinks()
onMounted(fetchOnce)

const categoryLabels: Record<string, string> = {
  kuro: '库洛',
  bilibili: 'B站',
  youtube: 'YouTube',
  miyoushe: '米游社',
  web: '活动',
}
const categoryBadge: Record<string, string> = {
  kuro: 'bg-blue-500/10 text-blue-700 dark:text-blue-300',
  bilibili: 'bg-pink-500/10 text-pink-700 dark:text-pink-300',
  youtube: 'bg-red-500/10 text-red-700 dark:text-red-300',
  miyoushe: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
  web: 'bg-slate-500/10 text-slate-600 dark:text-zinc-300',
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}
</script>

<template>
  <!-- 桌面侧栏列表 -->
  <div v-if="variant === 'list'">
    <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">官方链接</p>
    <p class="px-2 pb-2 text-[11px] leading-relaxed text-slate-400 dark:text-zinc-500">
      来自官方动态的活动、奖励与 H5 页面，点击直达；以官方页面为准。
    </p>
    <p v-if="!items.length" class="px-2 text-xs text-slate-400 dark:text-zinc-500">近期官方动态暂无外链。</p>
    <ul v-else class="space-y-0.5">
      <li v-for="link in items" :key="link.url">
        <a
          :href="link.url"
          target="_blank"
          rel="noopener"
          :title="`${link.title} · ${hostOf(link.url)}`"
          class="group block rounded-md px-2 py-1.5 transition-colors hover:bg-slate-200/70 dark:hover:bg-zinc-800/70"
        >
          <span class="flex items-center gap-1.5">
            <span
              class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
              :class="categoryBadge[link.category] ?? categoryBadge.web"
            >{{ categoryLabels[link.category] ?? '活动' }}</span>
            <span class="truncate text-[13px] text-slate-600 transition-colors group-hover:text-slate-900 dark:text-zinc-400 dark:group-hover:text-zinc-100">{{ link.title }}</span>
          </span>
          <span class="mono mt-0.5 block truncate pl-0.5 text-[11px] text-slate-400 dark:text-zinc-500">{{ hostOf(link.url) }} · {{ fmtTime(link.published_at) }}</span>
        </a>
      </li>
    </ul>
  </div>

  <!-- 移动端横滑条 -->
  <div v-else class="lg:hidden">
    <div class="mb-1.5 flex items-center justify-between px-1 text-xs text-slate-400 dark:text-zinc-500">
      <span class="font-medium text-slate-500 dark:text-zinc-400">官方活动链接</span>
      <span class="inline-flex items-center gap-1"><ExternalLink :size="11" /> 点击直达外部页面</span>
    </div>
    <p v-if="!items.length" class="px-1 pb-1 text-xs text-slate-400 dark:text-zinc-500">近期官方动态暂无外链。</p>
    <div v-else class="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 lg:mx-0 lg:hidden lg:px-0 [scrollbar-width:none]">
      <a
        v-for="link in items"
        :key="link.url"
        :href="link.url"
        target="_blank"
        rel="noopener"
        class="w-48 shrink-0 rounded-xl border border-slate-900/10 bg-white/70 p-2.5 transition-colors hover:border-blue-400/60 dark:border-white/10 dark:bg-white/5"
      >
        <span class="flex items-center gap-1.5">
          <span
            class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
            :class="categoryBadge[link.category] ?? categoryBadge.web"
          >{{ categoryLabels[link.category] ?? '活动' }}</span>
          <span class="mono truncate text-[11px] text-slate-400 dark:text-zinc-500">{{ fmtTime(link.published_at) }}</span>
        </span>
        <span class="mt-1 line-clamp-2 text-xs leading-snug text-slate-700 dark:text-zinc-200">{{ link.title }}</span>
      </a>
    </div>
  </div>
</template>
