<script setup lang="ts">
import { Bot, Check, Mail, Radar, ScanSearch } from 'lucide-vue-next'
import type { AccountOverview, CustomQuota, NotificationStatus } from '~/composables/useAccountCenter'
import { MAX_KEYWORDS } from '~/composables/useAccountCenter'
import { loginRoute } from '~/utils/safe-redirect'

useSeoMeta({ title: '订阅中心 · AniSignal', description: '关键词关注流、邮件早报、QQ 机器人、定制监控与 RSS：选择让游戏动态主动找到你的方式。' })

// 订阅中心：按"接收方式"组织。未登录时说明每种方式，登录后显示各自的当前状态并直达设置。
const { user, loaded } = useAuth()
interface BotClient { enabled: boolean, online: boolean, target_count: number }
interface Status { overview?: AccountOverview, notifications?: NotificationStatus, quota?: CustomQuota, bots?: BotClient[] }
const status = ref<Status | null>(null)

async function loadStatus() {
  const pick = <T,>(result: PromiseSettledResult<T>) => (result.status === 'fulfilled' ? result.value : undefined)
  const [overview, notifications, quota, bots] = await Promise.allSettled([
    $fetch<AccountOverview>('/api/account/overview'),
    $fetch<NotificationStatus>('/api/account/notifications'),
    $fetch<CustomQuota>('/api/account/custom-quota'),
    $fetch<BotClient[]>('/api/account/bot-clients'),
  ])
  status.value = { overview: pick(overview), notifications: pick(notifications), quota: pick(quota), bots: pick(bots) }
}
watch(user, (value) => {
  if (value) loadStatus()
  else status.value = null
}, { immediate: import.meta.client })

type Tone = 'on' | 'off' | 'idle'
interface Channel {
  key: string
  icon: typeof Mail
  tile: string
  title: string
  tag?: string
  text: string
  points: string[]
  to: string
  action: string
  extra?: { label: string, to: string }
  state: () => { tone: Tone, text: string } | null
}
const channels: Channel[] = [
  {
    key: 'keywords',
    icon: ScanSearch,
    tile: 'bg-brand-soft text-brand-ink',
    title: '关键词关注流',
    text: '把你关心的角色、版本和商品名设为关键词，站内新内容命中即收进你的关注流。',
    points: ['标题命中任一关键词即收录', '可限定游戏、排除噪音词', `最多 ${MAX_KEYWORDS} 个关键词`],
    to: '/account',
    action: '管理关键词',
    state: () => {
      const o = status.value?.overview
      if (!o) return null
      return o.keywords_enabled
        ? { tone: 'on', text: `${o.keywords_enabled} 个关键词启用 · ${o.feed_total.toLocaleString('zh-CN')} 条命中` }
        : { tone: 'idle', text: '还没有启用的关键词' }
    },
  },
  {
    key: 'email',
    icon: Mail,
    tile: 'bg-hsr-soft text-hsr-ink',
    title: '邮件通知',
    text: '每天早上收到一封按你偏好筛选的早报；重要变化也可以第一时间提醒。',
    points: ['每天 08:00 早报', '重要变化即时提醒（可选）', '随时手动生成 AI 精炼'],
    to: '/account?tab=notify',
    action: '邮件设置',
    state: () => {
      const n = status.value?.notifications
      if (!n) return null
      if (!n.enabled) return { tone: 'off', text: '未开启' }
      return { tone: 'on', text: `已开启 · ${n.instant_enabled ? '含重要变化提醒' : '仅每日早报'}` }
    },
  },
  {
    key: 'bot',
    icon: Bot,
    tile: 'bg-ai-soft text-ai-ink',
    title: 'QQ 机器人',
    text: '给自己的 AstrBot 装上 AniSignal 插件，在群里查活动、兑换码和商品，并按会话推送。',
    points: ['用账号中心签发的专用 Token 接入', '按会话选择推送的游戏与内容', '群聊、私聊都可用'],
    to: '/account?tab=notify#bot-access',
    action: '管理接入',
    extra: { label: '接入守则', to: '/bot-rules' },
    state: () => {
      const bots = status.value?.bots?.filter(bot => bot.enabled)
      if (!bots) return null
      if (!bots.length) return { tone: 'idle', text: '还没有接入' }
      const online = bots.filter(bot => bot.online).length
      const sessions = bots.reduce((sum, bot) => sum + bot.target_count, 0)
      return { tone: online ? 'on' : 'idle', text: `${bots.length} 个接入${online ? ` · ${online} 个在线` : ' · 等待连接'} · ${sessions} 个会话` }
    },
  },
  {
    key: 'custom',
    icon: Radar,
    tile: 'bg-arknights-soft text-arknights-ink',
    title: '定制监控',
    tag: '公测',
    text: '站内没收录的 UP 主或话题，可以自己建一个监控；公开后其他人也能在定制化频道看到。',
    points: ['跟踪 B 站 UP 主或搜索关键词', '可设播放量门槛与排除词', '新内容可邮件推送'],
    to: '/account?tab=custom',
    action: '管理监控',
    extra: { label: '浏览公开监控', to: '/custom' },
    state: () => {
      const q = status.value?.quota
      if (!q) return null
      if (!q.admitted) return { tone: 'off', text: '尚未开通公测名额' }
      return { tone: q.used ? 'on' : 'idle', text: `已用 ${q.used}/${q.max_feeds} 个监控` }
    },
  },
]
const DOT: Record<Tone, string> = { on: 'bg-ok', off: 'bg-faint', idle: 'bg-warn' }
const signedIn = computed(() => loaded.value && !!user.value)
</script>

<template>
  <div>
    <section class="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div class="wall-heading">
        <p class="mono mb-1.5 text-xs tracking-wide text-muted">选择接收信号的方式</p>
        <h1 class="text-[28px] font-bold leading-tight text-ink sm:text-[32px]">订阅中心</h1>
        <p class="mt-2 max-w-xl text-sm leading-relaxed text-muted">全站内容不登录也能看。想让新动态主动找到你，可以挑一种或几种方式组合使用。</p>
      </div>
      <NuxtLink v-if="!signedIn" :to="loginRoute('/watch')" class="btn-primary inline-flex h-10 items-center px-5">登录 / 注册</NuxtLink>
      <NuxtLink v-else to="/account" class="inline-flex h-10 items-center rounded-full px-4 text-sm text-ink-2 transition-colors hover:bg-chip hover:text-ink">账号中心 →</NuxtLink>
    </section>

    <div class="grid gap-5 md:grid-cols-2">
      <article v-for="channel in channels" :key="channel.key" class="panel flex flex-col p-5 sm:p-6">
        <header class="flex items-center gap-3">
          <span class="grid size-11 shrink-0 place-items-center rounded-xl" :class="channel.tile" aria-hidden="true">
            <component :is="channel.icon" :size="21" />
          </span>
          <h2 class="text-lg font-bold text-ink">{{ channel.title }}</h2>
          <span v-if="channel.tag" class="rounded bg-chip px-1.5 py-0.5 text-[11px] font-semibold text-muted">{{ channel.tag }}</span>
        </header>
        <p class="mt-3 text-sm leading-relaxed text-muted">{{ channel.text }}</p>
        <ul class="mt-3 space-y-1.5">
          <li v-for="point in channel.points" :key="point" class="flex items-start gap-2 text-[13px] text-ink-2">
            <Check :size="15" class="mt-0.5 shrink-0 text-ok" aria-hidden="true" />{{ point }}
          </li>
        </ul>

        <div class="mt-auto pt-5">
          <p class="flex items-center gap-2 rounded-[10px] bg-chip px-3 py-2.5 text-[13px]" :class="signedIn ? 'text-ink-2' : 'text-muted'">
            <template v-if="signedIn && channel.state()">
              <span class="size-2 shrink-0 rounded-full" :class="DOT[channel.state()!.tone]" aria-hidden="true" />
              {{ channel.state()!.text }}
            </template>
            <template v-else-if="signedIn">
              <span class="size-2 shrink-0 rounded-full bg-line" aria-hidden="true" />正在读取当前状态…
            </template>
            <template v-else>
              <span class="size-2 shrink-0 rounded-full bg-line" aria-hidden="true" />登录后可用
            </template>
          </p>
          <div class="mt-3 flex items-center gap-4">
            <NuxtLink :to="signedIn ? channel.to : loginRoute(channel.to)" class="btn-primary inline-flex h-9 items-center px-4">
              {{ signedIn ? channel.action : '登录后设置' }}
            </NuxtLink>
            <NuxtLink v-if="channel.extra" :to="channel.extra.to" class="link text-[13px]">{{ channel.extra.label }}</NuxtLink>
          </div>
        </div>
      </article>
    </div>

    <RssSubscribe class="mt-5" />
  </div>
</template>
