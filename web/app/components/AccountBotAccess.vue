<script setup lang="ts">
import { KeyRound, Plus } from 'lucide-vue-next'
import { fmtTime } from '~/utils/admin-types'

interface BotAccess {
  id: string; name: string; enabled: boolean; token_tail: string
  created_at: string; last_seen: string | null; online: boolean; version: string; target_count: number
}
const clients = ref<BotAccess[]>([])
const name = ref('我的 AstrBot')
const loading = ref(true)
const busy = ref('')
const error = ref('')
const message = ref('')
const issued = ref<{ name: string; token: string } | null>(null)
const visible = ref(false)

async function refresh() {
  loading.value = true
  try { clients.value = await $fetch<BotAccess[]>('/api/account/bot-clients') }
  catch { error.value = '机器人接入暂时加载失败，请重试。' }
  finally { loading.value = false }
}
onMounted(refresh)

async function action(key: string, run: () => Promise<void>) {
  if (busy.value) return
  busy.value = key
  error.value = message.value = ''
  try { await run() }
  catch (exc: any) { error.value = typeof exc?.data?.detail === 'string' ? exc.data.detail : '操作失败，请稍后重试。' }
  finally { busy.value = '' }
}
async function issue(row?: BotAccess) {
  if (row && !confirm(`重置「${row.name}」的 Token？原 Token 将立即失效，需要在 AstrBot 中填写新 Token。`)) return
  await action(row ? `rotate-${row.id}` : 'create', async () => {
    issued.value = await $fetch<{ name: string; token: string }>(row ? `/api/account/bot-clients/${row.id}/rotate` : '/api/account/bot-clients', {
      method: 'POST', ...(row ? {} : { body: { name: name.value } }),
    })
    visible.value = false
    message.value = 'Token 已生成，请复制到 AstrBot 的 AniSignal 插件配置。'
    await refresh()
  })
}
async function revoke(row: BotAccess) {
  if (!confirm(`撤销「${row.name}」的 Token 并暂停该接入的查询与推送？`)) return
  await action(`revoke-${row.id}`, async () => {
    await $fetch(`/api/account/bot-clients/${row.id}`, { method: 'DELETE' })
    issued.value = null
    message.value = 'Token 已撤销，该接入已暂停。'
    await refresh()
  })
}
async function copyToken() {
  if (!issued.value) return
  try {
    await navigator.clipboard.writeText(issued.value.token)
    message.value = 'Token 已复制。'
  } catch { error.value = '请点击 Token 输入框全选后复制。' }
}
</script>

<template>
  <section id="bot-access" class="panel scroll-mt-20 p-5" aria-labelledby="bot-access-title">
    <h2 id="bot-access-title" class="flex items-center gap-2 text-base font-semibold"><KeyRound class="size-4 text-muted" />机器人接入</h2>
    <p class="mt-2 text-sm text-muted">创建专用 Token，填入 AstrBot 的 AniSignal 插件，再按会话勾选内容和自动推送。</p>
    <form class="mt-4 flex flex-wrap items-end gap-3" @submit.prevent="issue()">
      <label class="min-w-0 flex-1 text-xs text-muted">接入名称<input v-model.trim="name" required maxlength="100" class="input mt-1 min-h-11 w-full px-3 py-2 text-sm" placeholder="例如：我的 AstrBot"></label>
      <button type="submit" class="btn-primary inline-flex min-h-11 items-center gap-1.5 px-3 text-sm" :disabled="!!busy"><Plus class="size-4" />{{ busy === 'create' ? '生成中…' : '创建 Token' }}</button>
    </form>
    <p v-if="error" role="alert" class="mt-3 text-sm text-red-600 dark:text-red-400">{{ error }}<button v-if="!clients.length" class="ml-2 underline" @click="error = ''; refresh()">重试</button></p>
    <p v-if="message" role="status" class="mt-3 text-sm text-ok">{{ message }}</p>
    <div v-if="issued" class="mt-4 rounded-xl border border-line bg-chip p-4">
      <p class="text-sm font-semibold">{{ issued.name }} · 新用户 Token</p>
      <p class="mt-1 text-xs text-muted">仅在本次展示，请立即复制并妥善保存。</p>
      <input :value="issued.token" :type="visible ? 'text' : 'password'" readonly aria-label="新用户 Token" class="input mt-3 min-h-11 w-full px-3 py-2 text-sm" @focus="($event.target as HTMLInputElement).select()">
      <div class="mt-2 flex flex-wrap gap-4"><button type="button" class="min-h-11 text-sm text-brand-ink" @click="copyToken">复制 Token</button><button type="button" class="min-h-11 text-sm text-brand-ink" @click="visible = !visible">{{ visible ? '隐藏' : '显示' }}</button><button type="button" class="min-h-11 text-sm text-muted" @click="issued = null">关闭</button></div>
    </div>
    <p v-if="loading" role="status" class="mt-4 text-sm text-muted">正在加载接入…</p>
    <p v-else-if="!clients.length && !error" class="mt-4 text-sm text-muted">尚未创建机器人接入。</p>
    <ul v-if="clients.length" class="mt-4 divide-y divide-line">
      <li v-for="row in clients" :key="row.id" class="flex flex-wrap items-center gap-3 py-3">
        <div class="min-w-0 flex-1"><p class="break-words text-sm font-medium">{{ row.name }} <span class="mono text-xs text-muted">•••• {{ row.token_tail }}</span></p><p class="mt-1 text-xs text-muted">{{ row.target_count }} 个会话 · {{ row.version || '等待鉴权' }}<span v-if="row.last_seen"> · {{ fmtTime(row.last_seen) }}</span></p></div>
        <span class="text-xs" :class="row.online ? 'text-ok' : 'text-muted'">{{ !row.enabled ? '已撤销' : row.online ? '已连接' : '等待连接' }}</span>
        <button v-if="row.enabled" type="button" class="min-h-11 text-xs text-brand-ink disabled:opacity-40" :disabled="!!busy" @click="issue(row)">{{ busy === `rotate-${row.id}` ? '重置中…' : '重置 Token' }}</button>
        <button v-if="row.enabled" type="button" class="min-h-11 text-xs text-muted disabled:opacity-40" :disabled="!!busy" @click="revoke(row)">{{ busy === `revoke-${row.id}` ? '撤销中…' : '撤销' }}</button>
      </li>
    </ul>
  </section>
</template>
