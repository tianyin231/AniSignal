<script setup lang="ts">
import { Bot, Check, Copy, Eye, EyeOff, Plus } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'
import { relativeTime, toTimestamp } from '~/utils/schedule'

// 机器人接入：为自己的 AstrBot 签发专用 Token；Token 只在签发当次展示。
interface BotAccess {
  id: string; name: string; enabled: boolean; token_tail: string
  created_at: string; last_seen: string | null; online: boolean; version: string; target_count: number
}
const { user } = useAuth()
const clients = ref<BotAccess[]>([])
const name = ref('我的 AstrBot')
const loading = ref(true)
const busy = ref('')
const error = ref('')
const message = ref('')
const issued = ref<{ name: string, token: string } | null>(null)
const visible = ref(false)
const copied = ref(false)
const now = ref(Date.now())
const active = computed(() => clients.value.filter(row => row.enabled))
const revoked = computed(() => clients.value.filter(row => !row.enabled))

function failure(exc: unknown, fallback: string) {
  if (apiErrorStatus(exc) === 401) user.value = null
  return apiErrorDetail(exc, fallback)
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    clients.value = await $fetch<BotAccess[]>('/api/account/bot-clients')
    now.value = Date.now()
  } catch (exc) {
    error.value = failure(exc, '机器人接入暂时加载失败，请重试。')
  } finally {
    loading.value = false
  }
}
onMounted(refresh)

async function action(key: string, run: () => Promise<void>) {
  if (busy.value) return
  busy.value = key
  error.value = message.value = ''
  try {
    await run()
  } catch (exc) {
    error.value = failure(exc, '操作失败，请稍后重试。')
  } finally {
    busy.value = ''
  }
}
async function issue(row?: BotAccess) {
  if (row && !confirm(`重置「${row.name}」的 Token？原 Token 会立即失效，需要在 AstrBot 中填写新 Token。`)) return
  await action(row ? `rotate-${row.id}` : 'create', async () => {
    issued.value = await $fetch<{ name: string, token: string }>(row ? `/api/account/bot-clients/${row.id}/rotate` : '/api/account/bot-clients', {
      method: 'POST', ...(row ? {} : { body: { name: name.value } }),
    })
    visible.value = copied.value = false
    await refresh()
  })
}
async function revoke(row: BotAccess) {
  if (!confirm(`撤销「${row.name}」的 Token？该接入的查询与推送会立即停止。`)) return
  await action(`revoke-${row.id}`, async () => {
    await $fetch(`/api/account/bot-clients/${row.id}`, { method: 'DELETE' })
    issued.value = null
    message.value = 'Token 已撤销，该接入已停止。'
    await refresh()
  })
}
async function copyToken() {
  if (!issued.value) return
  try {
    await navigator.clipboard.writeText(issued.value.token)
    copied.value = true
  } catch {
    error.value = '复制失败，请点击 Token 输入框全选后手动复制。'
  }
}
</script>

<template>
  <section id="bot-access" class="panel scroll-mt-24 p-5" aria-labelledby="bot-access-title">
    <header class="flex items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-ai-soft text-ai-ink" aria-hidden="true"><Bot :size="19" /></span>
      <div class="min-w-0 flex-1">
        <h2 id="bot-access-title" class="text-base font-bold text-ink">机器人接入</h2>
        <p class="mt-0.5 text-xs leading-relaxed text-muted">把 Token 填进 AstrBot 的 AniSignal 插件，再在插件里按会话选择推送内容。</p>
      </div>
      <NuxtLink to="/bot-rules" class="link shrink-0 text-[13px]">接入守则</NuxtLink>
    </header>

    <div v-if="issued" class="mt-4 rounded-xl border border-warn/30 bg-warn/5 p-4">
      <p class="text-sm font-semibold text-ink">「{{ issued.name }}」的新 Token</p>
      <p class="mt-0.5 text-xs text-warn">只显示这一次，请现在复制并填入插件配置。</p>
      <div class="relative mt-3">
        <input
          :value="issued.token"
          :type="visible ? 'text' : 'password'"
          readonly
          aria-label="新 Token"
          class="input mono h-10 w-full pr-10 text-xs"
          @focus="($event.target as HTMLInputElement).select()"
        >
        <button type="button" class="absolute inset-y-0 right-0 grid w-10 place-items-center text-faint hover:text-ink-2" :aria-label="visible ? '隐藏 Token' : '显示 Token'" @click="visible = !visible">
          <EyeOff v-if="visible" :size="16" /><Eye v-else :size="16" />
        </button>
      </div>
      <div class="mt-3 flex items-center gap-2">
        <button type="button" class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5" @click="copyToken">
          <Check v-if="copied" :size="15" /><Copy v-else :size="15" />{{ copied ? '已复制' : '复制 Token' }}
        </button>
        <button type="button" class="h-9 rounded-[10px] px-3 text-sm text-muted hover:bg-chip hover:text-ink" @click="issued = null">我已保存</button>
      </div>
    </div>

    <UiNotice v-if="error" tone="error" class="mt-4">
      {{ error }}<button v-if="!clients.length && !loading" type="button" class="ml-2 font-semibold underline" @click="refresh()">重试</button>
    </UiNotice>
    <UiNotice v-if="message" tone="ok" class="mt-4">{{ message }}</UiNotice>

    <p v-if="loading && !clients.length" role="status" class="mt-4 text-sm text-faint">正在加载接入…</p>
    <ul v-else-if="clients.length" class="mt-4 divide-y divide-line-soft border-y border-line-soft">
      <li v-for="row in [...active, ...revoked]" :key="row.id" class="flex flex-wrap items-center gap-x-3 gap-y-1 py-3" :class="row.enabled ? '' : 'opacity-60'">
        <span class="size-2 shrink-0 rounded-full" :class="!row.enabled ? 'bg-faint' : row.online ? 'bg-ok' : 'bg-warn'" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-semibold text-ink">{{ row.name }} <span class="mono text-xs font-normal text-faint">•••• {{ row.token_tail }}</span></p>
          <p class="mt-0.5 text-xs text-muted">
            {{ !row.enabled ? '已撤销' : row.online ? '已连接' : '等待插件连接' }}
            · {{ row.target_count }} 个会话
            <template v-if="row.version"> · v{{ row.version.replace(/^v/, '') }}</template>
            <template v-if="row.last_seen"> · {{ relativeTime(toTimestamp(row.last_seen), now) }}活跃</template>
          </p>
        </div>
        <div v-if="row.enabled" class="flex items-center gap-1 text-[13px]">
          <button type="button" class="h-8 rounded-lg px-2 font-medium text-brand-ink hover:bg-brand-soft" :disabled="!!busy" @click="issue(row)">
            {{ busy === `rotate-${row.id}` ? '重置中…' : '重置' }}
          </button>
          <button type="button" class="h-8 rounded-lg px-2 text-muted hover:bg-hot/10 hover:text-hot" :disabled="!!busy" @click="revoke(row)">
            {{ busy === `revoke-${row.id}` ? '撤销中…' : '撤销' }}
          </button>
        </div>
      </li>
    </ul>

    <form class="mt-4 flex gap-2" @submit.prevent="issue()">
      <input v-model.trim="name" required maxlength="100" aria-label="接入名称" placeholder="接入名称，例如：我的 AstrBot" class="input h-10 min-w-0 flex-1">
      <button type="submit" class="btn-primary inline-flex h-10 shrink-0 items-center gap-1 px-3.5" :disabled="!!busy || !name">
        <Plus :size="15" />{{ busy === 'create' ? '生成中…' : '新建 Token' }}
      </button>
    </form>
  </section>
</template>
