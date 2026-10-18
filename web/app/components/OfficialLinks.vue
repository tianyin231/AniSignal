<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'

// 官方动态外链直达（右侧栏，分区视角下展示）；频道首页的同一组链接在 ChannelHero 里以按钮呈现
const { community } = useGameSection()
const { items, fetchOnce } = useOfficialLinks(community)
const refreshVisible = () => { if (document.visibilityState === 'visible') void fetchOnce() }
let refreshTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  refreshVisible()
  refreshTimer = setInterval(refreshVisible, 60_000)
  document.addEventListener('visibilitychange', refreshVisible)
})
watch(community, () => fetchOnce())
onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', refreshVisible)
})

const categoryLabels: Record<string, string> = {
  kuro: '库洛',
  bilibili: 'B站',
  youtube: 'YouTube',
  miyoushe: '米游社',
  web: '活动',
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}

// tooltip：来源内容标题 + 链接名；两者相同（回退标签即标题）时只显示一份
function tooltip(link: { title?: string, label?: string, url: string }): string {
  const title = (link.title ?? '').trim()
  const label = (link.label || hostOf(link.url)).trim()
  if (!title || title === label) return label
  return `${title} · ${label}`
}
</script>

<template>
  <div>
    <h2 class="text-base font-bold text-ink">官方链接</h2>
    <p v-if="!items.length" class="mt-2 text-xs text-muted">近期官方动态暂无外链。</p>
    <ul v-else class="mt-2">
      <li v-for="link in items" :key="link.url" class="border-t border-line-soft first:border-t-0">
        <a
          :href="link.url"
          target="_blank"
          rel="noopener"
          :title="tooltip(link)"
          class="group block py-2"
        >
          <span class="flex items-center gap-2">
            <span class="shrink-0 rounded-md bg-chip px-1.5 py-0.5 text-[10px] font-semibold text-ink-2">{{ categoryLabels[link.category] ?? '活动' }}</span>
            <span class="truncate text-[13px] text-ink-2 transition-colors group-hover:text-brand-ink">{{ link.label || link.title }}</span>
          </span>
          <span class="mono mt-0.5 block truncate text-[11px] text-faint">{{ hostOf(link.url) }} · {{ fmtTime(link.published_at) }}</span>
        </a>
      </li>
    </ul>
  </div>
</template>
