<script setup lang="ts">
import { Plus, Send, Trash2, Webhook } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'
import { relativeTime, toTimestamp } from '~/utils/schedule'

interface WebhookRow {
  id: number
  name: string
  provider: string
  url: string
  has_secret: boolean
  enabled: boolean
  events_mode: string
  include_revisions: boolean
  last_status: string | null
  last_error: string | null
  last_sent_at: string | null
  created_at: string
}

const PROVIDERS = [
  { key: 'bark', label: 'Bark', urlHint: 'https://api.day.app/你的设备Key', secretHint: '选填：自建服务 Bearer Token' },
  { key: 'feishu', label: '飞书', urlHint: 'https://open.feishu.cn/open-apis/bot/v2/hook/...', secretHint: '选填：加签密钥' },
  { key: 'dingtalk', label: '钉钉', urlHint: 'https://oapi.dingtalk.com/robot/send?access_token=...', secretHint: '选填：加签 SEC 密钥' },
  { key: 'wecom', label: '企业微信', urlHint: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...', secretHint: '无需填写' },
  { key: 'discord', label: 'Discord', urlHint: 'https://discord.com/api/webhooks/...', secretHint: '无需填写' },
  { key: 'ntfy', label: 'ntfy', urlHint: 'https://ntfy.sh/你的主题名', secretHint: '选填：Access Token' },
  { key: 'generic', label: '通用 JSON', urlHint: 'https://example.com/webhook', secretHint: '选填：HMAC-SHA256 签名密钥' },
] as const

const PROVIDER_LABELS: Record<string, string> = Object.fromEntries(PROVIDERS.map(p => [p.key, p.label]))
const MODE_LABELS: Record<string, string> = {
  keywords: '命中关注词',
  all: '偏好分区重要事件',
  revisions: '仅官方原文修订',
}

const { user } = useAuth()
const items = ref<WebhookRow[]>([])
const maxWebhooks = ref(3)
const loading = ref(true)
const busy = ref('')
const error = ref('')
const message = ref('')
const formOpen = ref(false)
const now = ref(Date.now())

const form = reactive({
  name: '',
  provider: 'bark',
  url: '',
  secret: '',
  events_mode: 'keywords',
  include_revisions: false,
})

const currentProviderMeta = computed(() => PROVIDERS.find(p => p.key === form.provider) ?? PROVIDERS[0])

function failure(exc: unknown, fallback: string) {
  if (apiErrorStatus(exc) === 401) user.value = null
  return apiErrorDetail(exc, fallback)
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const res = await $fetch<{ items: WebhookRow[], max_webhooks: number }>('/api/account/webhooks')
    items.value = res.items
    maxWebhooks.value = res.max_webhooks
    now.value = Date.now()
  } catch (exc) {
    error.value = failure(exc, 'Webhook 列表加载失败，请重试。')
  } finally {
    loading.value = false
  }
}
onMounted(refresh)

async function create() {
  if (busy.value) return
  busy.value = 'create'
  error.value = message.value = ''
  try {
    await $fetch('/api/account/webhooks', {
      method: 'POST',
      body: {
        name: form.name.trim() || `${PROVIDER_LABELS[form.provider] ?? 'Webhook'} 推送`,
        provider: form.provider,
        url: form.url.trim(),
        secret: form.secret.trim() || null,
        events_mode: form.events_mode,
        include_revisions: form.events_mode === 'revisions' ? true : form.include_revisions,
      },
    })
    form.name = ''
    form.url = ''
    form.secret = ''
    form.include_revisions = false
    formOpen.value = false
    message.value = 'Webhook 已添加，可点击「测试」验证连通性。'
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '添加失败，请检查地址后重试。')
  } finally {
    busy.value = ''
  }
}

async function toggle(row: WebhookRow) {
  if (busy.value) return
  busy.value = `toggle-${row.id}`
  error.value = message.value = ''
  try {
    await $fetch(`/api/account/webhooks/${row.id}`, { method: 'PATCH', body: { enabled: !row.enabled } })
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '状态更新失败，请重试。')
  } finally {
    busy.value = ''
  }
}

async function toggleRevisions(row: WebhookRow) {
  if (busy.value) return
  busy.value = `rev-${row.id}`
  error.value = message.value = ''
  try {
    await $fetch(`/api/account/webhooks/${row.id}`, { method: 'PATCH', body: { include_revisions: !row.include_revisions } })
    message.value = !row.include_revisions ? `「${row.name}」已开启官方公告修订推送。` : `「${row.name}」已关闭官方公告修订推送。`
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '修订推送开关更新失败，请重试。')
  } finally {
    busy.value = ''
  }
}

async function testRow(row: WebhookRow) {
  if (busy.value) return
  busy.value = `test-${row.id}`
  error.value = message.value = ''
  try {
    const res = await $fetch<{ ok: boolean, status_code: number | null, error: string | null }>(`/api/account/webhooks/${row.id}/test`, { method: 'POST' })
    if (res.ok) {
      message.value = `「${row.name}」测试推送成功（HTTP ${res.status_code ?? 200}）。`
    } else {
      error.value = `「${row.name}」测试失败：${res.error || '目标未返回成功状态'}`
    }
    await refresh()
  } catch (exc) {
    error.value = failure(exc, '测试请求失败，请稍后再试。')
  } finally {
    busy.value = ''
  }
}

async function removeRow(row: WebhookRow) {
  if (busy.value) return
  if (!confirm(`删除 Webhook「${row.name}」？`)) return
  busy.value = `delete-${row.id}`
  error.value = message.value = ''
  try {
    await $fetch(`/api/account/webhooks/${row.id}`, { method: 'DELETE' })
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
  <section id="webhooks" class="panel scroll-mt-24 p-5" aria-labelledby="webhooks-title">
    <header class="flex items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-ink" aria-hidden="true"><Webhook :size="19" /></span>
      <div class="min-w-0 flex-1">
        <h2 id="webhooks-title" class="text-base font-bold text-ink">Webhook 推送</h2>
        <p class="mt-0.5 text-xs leading-relaxed text-muted">支持 Bark、飞书、钉钉、企业微信、Discord、ntfy 与通用 JSON，重要事件自动送达。</p>
      </div>
      <span class="shrink-0 text-xs tabular-nums text-muted">{{ items.length }}/{{ maxWebhooks }}</span>
    </header>

    <UiNotice v-if="error" tone="error" class="mt-4">{{ error }}</UiNotice>
    <UiNotice v-if="message" tone="ok" class="mt-4">{{ message }}</UiNotice>

    <p v-if="loading && !items.length" role="status" class="mt-4 text-sm text-faint">正在加载 Webhook…</p>
    <ul v-else-if="items.length" class="mt-4 divide-y divide-line-soft border-y border-line-soft">
      <li v-for="row in items" :key="row.id" class="py-3" :class="row.enabled ? '' : 'opacity-65'">
        <div class="flex items-center gap-2.5">
          <span
            class="size-2 shrink-0 rounded-full"
            :class="!row.enabled ? 'bg-faint' : row.last_status === 'ok' ? 'bg-ok' : row.last_status === 'failed' ? 'bg-hot' : 'bg-warn'"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="truncate text-sm font-semibold text-ink">{{ row.name }}</span>
              <span class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-ink-2">{{ PROVIDER_LABELS[row.provider] ?? row.provider }}</span>
              <span class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted">{{ MODE_LABELS[row.events_mode] ?? row.events_mode }}</span>
              <span v-if="row.include_revisions && row.events_mode !== 'revisions'" class="rounded bg-warn/15 px-1.5 py-0.5 text-[11px] font-medium text-warn">含官方修订</span>
            </div>
            <p class="mono mt-0.5 truncate text-[11px] text-faint">{{ row.url }}</p>
            <p v-if="row.last_sent_at || row.last_error" class="mt-0.5 text-xs" :class="row.last_status === 'failed' ? 'text-hot' : 'text-muted'">
              <template v-if="row.last_status === 'failed'">失败：{{ row.last_error || '未知错误' }}</template>
              <template v-else-if="row.last_sent_at">上次推送：{{ relativeTime(toTimestamp(row.last_sent_at), now) }}</template>
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
        <div class="mt-2 flex items-center gap-2 pl-4 text-xs">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2.5 font-medium text-ink-2 hover:bg-chip hover:text-ink disabled:opacity-40"
            :disabled="!!busy"
            @click="testRow(row)"
          >
            <Send :size="12" aria-hidden="true" />{{ busy === `test-${row.id}` ? '发送中…' : '测试' }}
          </button>
          <button
            v-if="row.events_mode !== 'revisions'"
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2.5 font-medium transition-colors disabled:opacity-40"
            :class="row.include_revisions ? 'border-warn/40 bg-warn/10 text-warn hover:bg-warn/15' : 'text-muted hover:bg-chip hover:text-ink'"
            :disabled="!!busy"
            @click="toggleRevisions(row)"
          >
            {{ busy === `rev-${row.id}` ? '更新中…' : row.include_revisions ? '已含官方修订' : '附加官方修订' }}
          </button>
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
      <div class="grid gap-2 sm:grid-cols-2">
        <input v-model="form.name" type="text" maxlength="64" placeholder="通道名称（如：我的 Bark）" class="input h-9 text-xs">
        <select v-model="form.provider" aria-label="Webhook 类型" class="select h-9 text-xs">
          <option v-for="p in PROVIDERS" :key="p.key" :value="p.key">{{ p.label }}</option>
        </select>
      </div>
      <input v-model="form.url" type="url" required maxlength="512" :placeholder="currentProviderMeta.urlHint" class="input mono h-9 w-full text-xs">
      <div class="grid gap-2 sm:grid-cols-2">
        <select v-model="form.events_mode" aria-label="触发范围" class="select h-9 text-xs">
          <option value="keywords">命中关注词的重要事件</option>
          <option value="all">偏好分区全部重要事件</option>
          <option value="revisions">仅官方公告修订（抓暗改）</option>
        </select>
        <input
          v-model="form.secret"
          type="password"
          maxlength="256"
          autocomplete="off"
          :placeholder="currentProviderMeta.secretHint"
          class="input h-9 text-xs"
        >
      </div>
      <label v-if="form.events_mode !== 'revisions'" class="flex items-center gap-2 text-xs text-ink-2">
        <input v-model="form.include_revisions" type="checkbox" class="accent-brand">
        <span>同时推送官方公告实质修订（抓暗改）</span>
      </label>
      <div class="flex items-center gap-2 pt-1">
        <button type="submit" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5 text-xs" :disabled="!!busy">
          {{ busy === 'create' ? '保存中…' : '保存通道' }}
        </button>
        <button type="button" class="h-9 rounded-[10px] px-3 text-xs text-muted hover:bg-chip hover:text-ink" @click="formOpen = false">取消</button>
      </div>
    </form>

    <div v-else-if="items.length < maxWebhooks" class="mt-4">
      <button
        type="button"
        class="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-line px-3.5 text-[13px] font-semibold text-brand-ink transition-colors hover:bg-brand-soft"
        @click="formOpen = true"
      >
        <Plus :size="15" aria-hidden="true" />添加 Webhook 通道
      </button>
    </div>
  </section>
</template>
