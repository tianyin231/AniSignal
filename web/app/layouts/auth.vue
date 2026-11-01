<script setup lang="ts">
import { Bot, Mail, Radar, ScanSearch } from 'lucide-vue-next'
import type { ApiEventList, PublicStats } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { beijingParts, relativeTime, toTimestamp } from '~/utils/schedule'
import { decodeEntities } from '~/utils/text'

// 认证布局：品牌头 + 表单 + （桌面）登录后能做什么与站内实时动态。
// 登录 / 注册验证成功、或已登录的人打开认证页时，统一在这里跳回来源页。
const { user, loaded } = useAuth()
const { target } = useAuthRedirect()
watch([loaded, user], () => {
  if (loaded.value && user.value) navigateTo(target(), { replace: true })
}, { immediate: true })

const { api } = usePublicApi()
const showPreview = ref(false)
let previewMedia: MediaQueryList | undefined
const updatePreview = () => { showPreview.value = previewMedia?.matches ?? false }
onMounted(() => {
  previewMedia = window.matchMedia('(min-width: 1024px)')
  updatePreview()
  previewMedia.addEventListener('change', updatePreview)
})
onBeforeUnmount(() => previewMedia?.removeEventListener('change', updatePreview))

const now = ref(Date.now())
const { data: stats } = useLazyAsyncData('auth-stats', (_app, { signal }) => api<PublicStats>('/api/stats', { signal }), {
  server: false, immediate: false, watch: [showPreview],
})
const { data: eventsData } = useLazyAsyncData(
  'auth-events',
  (_app, { signal }) => api<ApiEventList>('/api/events', { query: { limit: 4, community: 'all' }, signal }),
  { server: false, immediate: false, watch: [showPreview] },
)
const events = computed(() => (eventsData.value?.items ?? []).map((event) => {
  const meta = communityMeta(event.community)
  return {
    id: event.id,
    title: decodeEntities(event.title),
    game: meta ? (meta.key === 'ai' ? 'AI 快讯' : meta.label) : '',
    dot: meta ? communityClasses(meta.color).dot : 'bg-faint',
    time: relativeTime(toTimestamp(event.latest_evidence_at ?? event.first_seen_at), now.value),
  }
}))
const dateLine = computed(() => {
  const p = beijingParts(now.value)
  return `${p.year}.${String(p.month).padStart(2, '0')}.${String(p.day).padStart(2, '0')} ${p.weekday}`
})
const features = [
  { icon: ScanSearch, title: '关键词关注流', text: '角色、版本、商品名命中即收录，可按游戏筛选、排除噪音。' },
  { icon: Mail, title: '邮件早报', text: '每天 08:00 汇总；重要变化可单独即时提醒。' },
  { icon: Bot, title: 'QQ 机器人', text: '接入 AstrBot，在群里查询活动并接收推送。' },
  { icon: Radar, title: '定制监控', text: '跟踪指定 B 站 UP 主或关键词，可公开到定制化频道。' },
]
</script>

<template>
  <div class="flex min-h-dvh flex-col">
    <header class="glass sticky top-0 z-40">
      <div class="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
        <NuxtLink to="/" class="flex items-center gap-2.5" aria-label="AniSignal 首页">
          <img src="/icon-64.webp" alt="" width="32" height="32" class="size-8 shrink-0 rounded-[9px]">
          <span class="num text-[19px] font-bold tracking-tight text-ink">AniSignal</span>
        </NuxtLink>
        <div class="flex items-center gap-1">
          <ThemeToggle />
          <NuxtLink to="/" class="ml-1 inline-flex h-9 items-center rounded-full px-3.5 text-sm text-ink-2 transition-colors hover:bg-chip hover:text-ink">
            先逛逛 →
          </NuxtLink>
        </div>
      </div>
    </header>
    <AnnouncementBanner />

    <main class="mx-auto grid w-full max-w-6xl flex-1 items-center gap-12 px-4 py-8 sm:px-6 sm:py-12 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)] lg:gap-16 lg:py-16">
      <div class="panel w-full max-w-[420px] justify-self-center p-6 sm:p-8 lg:justify-self-start">
        <slot />
      </div>

      <aside v-if="showPreview" class="space-y-5" aria-label="登录后可用的功能">
        <div class="wall-heading">
          <p class="mono text-xs tracking-wide text-muted">{{ dateLine }}</p>
          <h2 class="mt-1.5 text-[28px] font-bold leading-tight text-ink">登录后，信号会主动找到你</h2>
          <p class="mt-2 text-sm text-muted">全站内容不登录也能看；账号用来保存你的关注与通知方式。</p>
        </div>
        <ul class="grid grid-cols-2 gap-3">
          <li v-for="feature in features" :key="feature.title" class="panel p-4">
            <span class="grid size-9 place-items-center rounded-[10px] bg-brand-soft text-brand-ink" aria-hidden="true">
              <component :is="feature.icon" :size="18" />
            </span>
            <p class="mt-3 text-sm font-bold text-ink">{{ feature.title }}</p>
            <p class="mt-1 text-xs leading-relaxed text-muted">{{ feature.text }}</p>
          </li>
        </ul>
        <section v-if="events.length" class="panel px-5 pb-2 pt-4" aria-labelledby="auth-live-title">
          <header class="flex items-baseline justify-between gap-3">
            <h3 id="auth-live-title" class="text-base font-bold text-ink">正在发生</h3>
            <span v-if="stats" class="text-xs text-muted">已收录 <span class="num font-semibold text-ink-2">{{ stats.total_contents.toLocaleString('zh-CN') }}</span> 条</span>
          </header>
          <ul class="mt-2">
            <li v-for="event in events" :key="event.id" class="border-t border-line-soft">
              <NuxtLink :to="`/events/${event.id}`" class="group block py-2.5">
                <span class="line-clamp-1 text-sm font-semibold text-ink group-hover:text-brand-ink">{{ event.title }}</span>
                <span class="mt-1 flex items-center gap-1.5 text-xs text-muted">
                  <span class="size-[7px] rounded-full" :class="event.dot" aria-hidden="true" />
                  {{ event.game }}
                  <span class="mono text-faint">· {{ event.time }}</span>
                </span>
              </NuxtLink>
            </li>
          </ul>
        </section>
      </aside>
    </main>

    <footer class="mx-auto flex w-full max-w-6xl flex-wrap gap-x-4 gap-y-1 px-4 pb-8 text-xs text-faint sm:px-6">
      <NuxtLink to="/about" class="hover:text-ink-2">关于 AniSignal</NuxtLink>
      <NuxtLink to="/terms" class="hover:text-ink-2">用户条款</NuxtLink>
      <NuxtLink to="/privacy" class="hover:text-ink-2">隐私政策</NuxtLink>
    </footer>
  </div>
</template>
