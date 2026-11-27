<script setup lang="ts">
import { ExternalLink, FileText, Plus, Send, Trash2 } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'
import { REPORT_PERIOD_LABELS, WEBHOOK_PROVIDER_LABELS } from '~/utils/lexicon'
import { relativeTime, toTimestamp } from '~/utils/schedule'

interface ReportSubRow {
  id: number
  name: string
  scope_type: 'community' | 'custom_bundle'
  scope_key: string
  scope_label: string
  period: 'daily' | 'weekly'
  channel: 'email' | 'webhook'
  webhook_id: number | null
  webhook_name: string | null
  webhook_provider: string | null
  enabled: boolean
  last_period_key: string | null
  last_report_id: number | null
  last_status: string | null
  last_error: string | null
  last_sent_at: string | null
}

interface CommunityOption {
  key: string
  label: string
  domain: string
}

interface WebhookOption {
  id: number
  name: string
  provider: string
  enabled: boolean
}

interface SubscriptionsResp {
  items: ReportSubRow[]
  max_subscriptions: number
  schedule_hint: string
  communities: CommunityOption[]
  webhooks: WebhookOption[]
}

const emit = defineEmits<{ openWebhooks: [] }>()
const { user } = useAuth()

const items = ref<ReportSubRow[]>([])
const communities = ref<CommunityOption[]>([])
const webhooks = ref<WebhookOption[]>([])
const maxSubs = ref(5)
const scheduleHint = ref('日报每天 08:30、周报每周一 08:30（北京时间）')
const loading = ref(true)
const busy = ref('')
const error = ref('')
const message = ref('')
const formOpen = ref(false)
const now = ref(Date.now())

const form = reactive({
  target: 'custom_bundle',
  period: 'weekly' as 'daily' | 'weekly',
  channel: 'email' as 'email' | 'webhook',
  webhook_id: 0,
  name: '',
})

function failure(exc: unknown, fallback: string) {
  if (apiErrorStatus(exc) === 401) user.value = null
  return apiErrorDetail(exc, fallback)
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const res = await $fetch<SubscriptionsResp>('/api/account/reports/subscriptions')
    items.value = res.items
    communities.value = res.communities
    webhooks.value = res.webhooks
    maxSubs.value = res.max_subscriptions
    scheduleHint.value = res.schedule_hint
    if (!form.webhook_id && res.webhooks.length) {
      form.webhook_id = res.webhooks[0]!.id
    }
    now.value = Date.now()
  } catch (exc) {
    error.value = failure(exc, '周期报告订阅加载失败，请重试。')
  } finally {
    loading.value = false
  }
}
onMounted(refresh)

function channelBadge(row: ReportSubRow): string {
  if (row.channel === 'email') return '邮件'
  const prov = row.webhook_provider ? (WEBHOOK_PROVIDER_LABELS[row.webhook_provider] ?? row.webhook_provider) : 'Webhook'
  return row.webhook_name ? `${prov} · ${row.webhook_name}` : prov
}

async function create() {
  if (busy.value) return
  if (form.channel === 'webhook' && !form.webhook_id) {
    error.value = '请先添加一个 Webhook 通道。'
    return
  }
  busy.value = 'create'
  error.value = message.value = ''
  const isCustom = form.target === 'custom_bundle'
  try {
    await $fetch('/api/account/reports/subscriptions', {
      method: 'POST',
      body: {
        name: form.name.trim(),
        scope_type: isCustom ? 'custom_bundle' : 'community',
        scope_key: isCustom ? 'all' : form.target,
        period: form.period,
        channel: form.channel,
        webhook_id: form.channel === 'webhook' ? form.webhook_id : null,
      },
    })
    form.name = ''
    formOpen.value = false
    message.value = '周期报告订阅已创建，可点击「立即发送」生成并验证效果。'
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '创建失败，请检查选项后重试。')
  } finally {
    busy.value = ''
  }
}

async function toggle(row: ReportSubRow) {
  if (busy.value) return
  busy.value = `toggle-${row.id}`
  error.value = message.value = ''
  try {
    await $fetch(`/api/account/reports/subscriptions/${row.id}`, {
      method: 'PATCH',
      body: { enabled: !row.enabled },
    })
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '状态更新失败，请重试。')
  } finally {
    busy.value = ''
  }
}

async function sendNow(row: ReportSubRow) {
  if (busy.value) return
  busy.value = `send-${row.id}`
  error.value = message.value = ''
  try {
    const res = await $fetch<{ ok: boolean, status: string, report_id: number | null, item_count?: number, error: string | null }>(
      `/api/account/reports/subscriptions/${row.id}/send`,
      { method: 'POST', body: { force: false } },
    )
    if (res.ok) {
      message.value = `「${row.name}」已发送（本期汇总 ${res.item_count ?? 0} 条内容）。`
    } else {
      error.value = `「${row.name}」未发送：${res.error || '请稍后重试'}`
    }
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '立即发送失败，请稍后重试。')
  } finally {
    busy.value = ''
  }
}

async function removeRow(row: ReportSubRow) {
  if (busy.value) return
  if (!confirm(`删除周期报告订阅「${row.name}」？`)) return
  busy.value = `delete-${row.id}`
  error.value = message.value = ''
  try {
    await $fetch(`/api/account/reports/subscriptions/${row.id}`, { method: 'DELETE' })
    message.value = `已删除「${row.name}」。`
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '删除失败，请重试。')
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <section id="periodic-reports" class="panel scroll-mt-24 p-5" aria-labelledby="periodic-reports-title">
    <header class="flex flex-wrap items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-ink" aria-hidden="true">
        <FileText :size="19" />
      </span>
      <div class="min-w-0 flex-1">
        <h2 id="periodic-reports-title" class="text-base font-bold text-ink">周期报告推送</h2>
        <p class="mt-0.5 text-xs leading-relaxed text-muted">{{ scheduleHint }}</p>
      </div>
      <div class="flex shrink-0 items-center gap-3 text-xs">
        <NuxtLink to="/reports" class="font-medium text-brand-ink hover:underline">报告归档 →</NuxtLink>
        <span class="tabular-nums text-muted">{{ items.length }}/{{ maxSubs }}</span>
      </div>
    </header>

    <UiNotice v-if="error" tone="error" class="mt-4">{{ error }}</UiNotice>
    <UiNotice v-if="message" tone="ok" class="mt-4">{{ message }}</UiNotice>

    <p v-if="loading && !items.length" role="status" class="mt-4 text-sm text-faint">正在加载订阅…</p>
    <ul v-else-if="items.length" class="mt-4 divide-y divide-line-soft border-y border-line-soft">
      <li v-for="row in items" :key="row.id" class="py-3" :class="row.enabled ? '' : 'opacity-65'">
        <div class="flex items-center gap-2.5">
          <span
            class="size-2 shrink-0 rounded-full"
            :class="!row.enabled ? 'bg-faint' : row.last_status === 'sent' ? 'bg-ok' : row.last_status === 'failed' ? 'bg-hot' : 'bg-warn'"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="truncate text-sm font-semibold text-ink">{{ row.name }}</span>
              <span class="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-medium text-brand-ink">
                {{ REPORT_PERIOD_LABELS[row.period] ?? row.period }}
              </span>
              <span class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-ink-2">{{ row.scope_label }}</span>
              <span class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted">{{ channelBadge(row) }}</span>
            </div>
            <p v-if="row.last_sent_at || row.last_error" class="mt-0.5 text-xs" :class="row.last_status === 'failed' ? 'text-hot' : 'text-muted'">
              <template v-if="row.last_status === 'failed'">失败：{{ row.last_error || '推送异常' }}</template>
              <template v-else-if="row.last_status === 'skipped' && row.last_error">跳过：{{ row.last_error }}</template>
              <template v-else-if="row.last_sent_at">上次发送：{{ relativeTime(toTimestamp(row.last_sent_at), now) }}（{{ row.last_period_key }}）</template>
            </p>
          </div>
          <UiSwitch
            :on="row.enabled"
            :label="row.enabled ? `停用 ${row.name}` : `启用 ${row.name}`"
            :disabled="!!busy"
            :busy="busy === `toggle-${row.id}`"
            @toggle="toggle(row)"
          />
        </div>

        <div class="mt-2 flex flex-wrap items-center gap-2 pl-4 text-xs">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2.5 font-medium text-ink-2 hover:bg-chip hover:text-ink disabled:opacity-40"
            :disabled="!!busy"
            @click="sendNow(row)"
          >
            <Send :size="12" aria-hidden="true" />
            {{ busy === `send-${row.id}` ? '生成发送中…' : '立即发送' }}
          </button>
          <NuxtLink
            v-if="row.last_report_id"
            :to="`/reports/${row.last_report_id}`"
            class="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2.5 font-medium text-brand-ink hover:bg-brand-soft"
          >
            查看最近一期 <ExternalLink :size="11" aria-hidden="true" />
          </NuxtLink>
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-lg px-2 text-muted hover:bg-hot/10 hover:text-hot disabled:opacity-40"
            :disabled="!!busy"
            @click="removeRow(row)"
          >
            <Trash2 :size="12" aria-hidden="true" />删除
          </button>
        </div>
      </li>
    </ul>

    <form v-if="formOpen" class="mt-4 space-y-2.5 rounded-xl border border-line bg-chip/35 p-3.5" @submit.prevent="create">
      <div class="grid gap-2 sm:grid-cols-3">
        <select v-model="form.target" aria-label="订阅对象" class="select h-9 text-xs">
          <option value="custom_bundle">我的定制监控合集</option>
          <optgroup label="站内分区">
            <option v-for="c in communities" :key="c.key" :value="c.key">{{ c.label }}</option>
          </optgroup>
        </select>

        <select v-model="form.period" aria-label="发送周期" class="select h-9 text-xs">
          <option value="weekly">每周汇总（周一 08:30）</option>
          <option value="daily">每日汇总（每天 08:30）</option>
        </select>

        <select v-model="form.channel" aria-label="送达方式" class="select h-9 text-xs">
          <option value="email">邮箱送达</option>
          <option value="webhook">Webhook 推送</option>
        </select>
      </div>

      <div v-if="form.channel === 'webhook'" class="flex flex-wrap items-center gap-2">
        <select v-if="webhooks.length" v-model.number="form.webhook_id" aria-label="选择 Webhook 通道" class="select h-9 flex-1 text-xs">
          <option v-for="wh in webhooks" :key="wh.id" :value="wh.id">
            {{ wh.name }}（{{ WEBHOOK_PROVIDER_LABELS[wh.provider] ?? wh.provider }}{{ wh.enabled ? '' : ' · 已停用' }}）
          </option>
        </select>
        <p v-else class="flex items-center gap-2 text-xs text-muted">
          尚未配置 Webhook 通道。
          <button type="button" class="font-semibold text-brand-ink hover:underline" @click="emit('openWebhooks')">
            前往添加 Webhook
          </button>
        </p>
      </div>

      <input
        v-model="form.name"
        type="text"
        maxlength="64"
        placeholder="订阅名称（留空自动生成，如：开源周报）"
        class="input h-9 w-full text-xs"
      >

      <div class="flex items-center gap-2 pt-1">
        <button type="submit" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5 text-xs" :disabled="!!busy || (form.channel === 'webhook' && !webhooks.length)">
          {{ busy === 'create' ? '保存中…' : '保存订阅' }}
        </button>
        <button type="button" class="h-9 rounded-[10px] px-3 text-xs text-muted hover:bg-chip hover:text-ink" @click="formOpen = false">取消</button>
      </div>
    </form>

    <div v-else-if="items.length < maxSubs" class="mt-4">
      <button
        type="button"
        class="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-line px-3.5 text-[13px] font-semibold text-brand-ink transition-colors hover:bg-brand-soft"
        @click="formOpen = true"
      >
        <Plus :size="15" aria-hidden="true" />添加报告订阅
      </button>
    </div>
  </section>
</template>
