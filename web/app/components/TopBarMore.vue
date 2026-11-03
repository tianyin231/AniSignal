<script setup lang="ts">
import { BarChart3, BellRing, ChevronDown, HeartPulse } from 'lucide-vue-next'
import GitHubMark from '~/components/GitHubMark.vue'
import type { RouteLocationRaw } from 'vue-router'

// 顶栏"更多"：放不下的频道（按宽度自动收纳）+ 全站工具页入口。
// 频道越来越多时顶栏不再横向溢出，工具页（数据统计等）也不用只靠页脚。
export interface MoreChannel { key: string, label: string, to: RouteLocationRaw, dot: string }
defineProps<{ channels: MoreChannel[] }>()

const TOOLS = [
  { to: '/stats', label: '数据统计', hint: '每日入库量与采集趋势', icon: BarChart3 },
  { to: '/sources', label: '来源健康', hint: '各来源的抓取状态', icon: HeartPulse },
  { to: '/watch', label: '订阅中心', hint: '关注流、邮件与机器人', icon: BellRing },
  { to: '/project', label: '开源与更新', hint: '项目代码、贡献者与提交记录', icon: GitHubMark },
] as const

const route = useRoute()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const button = ref<HTMLButtonElement | null>(null)
const toolActive = computed(() => TOOLS.some(tool => tool.to === route.path))

function onDocClick(event: MouseEvent) {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) {
    open.value = false
    button.value?.focus()
  }
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
watch(() => route.fullPath, () => { open.value = false })
</script>

<template>
  <div ref="root" class="relative flex shrink-0 items-stretch">
    <button
      ref="button"
      type="button"
      class="flex items-center gap-0.5 whitespace-nowrap border-b-2 px-3 text-sm transition-colors"
      :class="toolActive ? 'border-brand font-semibold text-ink' : 'border-transparent text-muted hover:text-ink'"
      :aria-expanded="open"
      aria-haspopup="menu"
      @click="open = !open"
    >
      更多<ChevronDown :size="14" class="transition-transform" :class="{ 'rotate-180': open }" aria-hidden="true" />
    </button>
    <div v-if="open" class="popover absolute right-0 top-full z-50 mt-1 w-64 p-1.5" role="menu" aria-label="更多">
      <template v-if="channels.length">
        <p class="px-3 pb-1 pt-1.5 text-[11px] font-semibold tracking-wide text-faint">频道</p>
        <NuxtLink
          v-for="ch in channels"
          :key="ch.key"
          :to="ch.to"
          role="menuitem"
          class="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-chip hover:text-ink"
        >
          <span class="size-2 shrink-0 rounded-full" :class="ch.dot" aria-hidden="true" />{{ ch.label }}
        </NuxtLink>
        <div class="mx-2 my-1.5 border-t border-line-soft" />
      </template>
      <p class="px-3 pb-1 pt-1.5 text-[11px] font-semibold tracking-wide text-faint">全站</p>
      <NuxtLink
        v-for="tool in TOOLS"
        :key="tool.to"
        :to="tool.to"
        role="menuitem"
        class="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-chip"
        :aria-current="route.path === tool.to ? 'page' : undefined"
      >
        <span class="grid size-8 shrink-0 place-items-center rounded-lg bg-chip text-ink-2" aria-hidden="true"><component :is="tool.icon" :size="16" /></span>
        <span class="min-w-0">
          <span class="block text-sm font-medium" :class="route.path === tool.to ? 'text-brand-ink' : 'text-ink'">{{ tool.label }}</span>
          <span class="block truncate text-xs text-muted">{{ tool.hint }}</span>
        </span>
      </NuxtLink>
    </div>
  </div>
</template>
