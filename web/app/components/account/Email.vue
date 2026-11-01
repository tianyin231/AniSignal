<script setup lang="ts">
import { Clock3, Mail, Sparkles } from 'lucide-vue-next'
import type { NotificationState } from '~/composables/useAccountCenter'
import { relativeTime, toTimestamp } from '~/utils/schedule'

// 邮件通知：总开关 + 重要变化即时提醒 + 手动发送 + 最近发送记录。
const emit = defineEmits<{ addKeyword: [] }>()
const center = useAccountCenter()
const { notifications, notifyBusy, notifyMessage, notifyError, notificationReady, aiReady, notificationBlockReason, enabledKeywords, loading, errors } = center
const now = ref(Date.now())
const STATUS: Record<NotificationState, { label: string, tone: string }> = {
  pending: { label: '生成中', tone: 'bg-chip text-muted' },
  sending: { label: '发送中', tone: 'bg-chip text-muted' },
  sent: { label: '已发送', tone: 'bg-ok/10 text-ok' },
  skipped: { label: '已跳过', tone: 'bg-chip text-faint' },
  failed: { label: '失败', tone: 'bg-hot/10 text-hot' },
}
const KIND: Record<string, string> = {
  morning: '每日早报', ai: 'AI 精炼', instant: '重要变化', custom: '定制监控', admin: '管理员通知', broadcast: '站点公告',
}
const records = computed(() => (notifications.value?.records ?? []).slice(0, 5).map(record => ({
  ...record,
  kindLabel: KIND[record.kind] ?? '其他通知',
  status: STATUS[record.status],
  time: relativeTime(toTimestamp(record.sent_at ?? record.created_at), now.value),
})))
const needsKeyword = computed(() => notificationReady.value && !loading.keywords && !errors.keywords && !enabledKeywords.value.length)
</script>

<template>
  <section id="email-notifications" class="panel scroll-mt-24 p-5" aria-labelledby="email-title">
    <header class="flex items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-ink" aria-hidden="true"><Mail :size="19" /></span>
      <div class="min-w-0 flex-1">
        <h2 id="email-title" class="text-base font-bold text-ink">邮件通知</h2>
        <p class="mt-0.5 text-xs text-muted">发到 {{ center.user.value?.email }}</p>
      </div>
      <UiSwitch
        :on="notifications?.enabled ?? false"
        :label="notifications?.enabled ? '关闭邮件通知' : '开启邮件通知'"
        :disabled="loading.notifications || !!errors.notifications || (!!notifyBusy && notifyBusy !== 'toggle')"
        :busy="notifyBusy === 'toggle'"
        @toggle="center.toggleEmail()"
      />
    </header>

    <UiNotice v-if="errors.notifications" tone="warn" class="mt-4">
      {{ errors.notifications }}<button type="button" class="ml-2 font-semibold underline" @click="center.fetchNotifications()">重试</button>
    </UiNotice>

    <dl class="mt-4 divide-y divide-line-soft border-y border-line-soft text-sm">
      <div class="flex items-center gap-3 py-3">
        <div class="min-w-0 flex-1">
          <dt class="font-semibold text-ink">每日早报</dt>
          <dd class="mt-0.5 text-xs leading-relaxed text-muted">{{ notifications?.schedule ?? '每天 08:00（北京时间）' }}，按个人降噪筛选后汇总</dd>
        </div>
        <span class="shrink-0 text-xs font-medium" :class="notifications?.enabled ? 'text-ok' : 'text-faint'">{{ notifications?.enabled ? '随总开关开启' : '未开启' }}</span>
      </div>
      <div class="flex items-center gap-3 py-3">
        <div class="min-w-0 flex-1">
          <dt class="font-semibold text-ink">重要变化即时提醒</dt>
          <dd class="mt-0.5 text-xs leading-relaxed text-muted">关注词命中的重要事件，以及官方版本、发售、发货等新变化；不补发历史。</dd>
        </div>
        <UiSwitch
          :on="notifications?.instant_enabled ?? false"
          :label="notifications?.instant_enabled ? '关闭重要变化提醒' : '开启重要变化提醒'"
          :disabled="!notificationReady || (!notifications?.instant_enabled && !aiReady) || (!!notifyBusy && notifyBusy !== 'instant')"
          :busy="notifyBusy === 'instant'"
          @toggle="center.toggleInstant()"
        />
      </div>
    </dl>

    <UiNotice v-if="notificationBlockReason" tone="info" class="mt-4">
      {{ notificationBlockReason }}
      <button v-if="needsKeyword" type="button" class="link mt-1 block font-semibold" @click="emit('addKeyword')">去添加关键词</button>
    </UiNotice>
    <UiNotice v-if="notifications?.instant_enabled && !notifications.enabled" tone="info" class="mt-3">即时提醒的设置已保留，开启邮件通知后恢复。</UiNotice>

    <div class="mt-4 grid gap-2 sm:grid-cols-2">
      <button type="button" class="btn-primary inline-flex h-10 items-center justify-center gap-1.5 px-3" :disabled="!!notifyBusy || !notificationReady" @click="center.sendNotification('morning')">
        <Mail :size="15" /> {{ notifyBusy === 'morning' ? '提交中…' : '发送今日早报' }}
      </button>
      <button
        type="button"
        class="inline-flex h-10 items-center justify-center gap-1.5 rounded-[10px] border border-line px-3 text-sm font-semibold text-brand-ink transition-colors hover:bg-brand-soft"
        :disabled="!!notifyBusy || !aiReady"
        @click="center.sendNotification('ai')"
      >
        <Sparkles :size="15" /> {{ notifyBusy === 'ai' ? '提交中…' : '生成 AI 精炼' }}
      </button>
    </div>
    <p class="mt-2 flex items-start gap-1.5 text-xs leading-relaxed text-faint">
      <Clock3 :size="13" class="mt-0.5 shrink-0" aria-hidden="true" /> AI 会复核并总结最近 24 小时的内容，通常需要几分钟，完成后自动发送。
    </p>
    <UiNotice v-if="notifyMessage" :tone="notifications?.enabled ? 'ok' : 'info'" class="mt-3">{{ notifyMessage }}</UiNotice>
    <UiNotice v-if="notifyError" tone="error" class="mt-3">{{ notifyError }}</UiNotice>

    <div v-if="records.length" class="mt-5">
      <h3 class="text-xs font-semibold text-muted">最近发送</h3>
      <ul class="mt-2 divide-y divide-line-soft">
        <li v-for="record in records" :key="record.id" class="py-2.5">
          <div class="flex items-center gap-2 text-xs">
            <span class="font-semibold text-ink-2">{{ record.kindLabel }}</span>
            <span class="rounded px-1.5 py-0.5 text-[11px] font-medium" :class="record.status.tone">{{ record.status.label }}</span>
            <span class="mono ml-auto text-faint">{{ record.time }}</span>
          </div>
          <p v-if="record.subject" class="mt-1 line-clamp-1 text-[13px] text-muted">{{ record.subject }}</p>
          <p v-if="record.error" class="mt-0.5 text-xs text-faint">{{ record.error }}</p>
          <p v-else-if="record.content_count > 0" class="mt-0.5 text-xs text-faint">{{ record.content_count }} 条内容</p>
        </li>
      </ul>
    </div>
  </section>
</template>
