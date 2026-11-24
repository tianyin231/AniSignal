<script setup lang="ts">
import { Check, Copy, Cpu, RefreshCw } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'
import { copyText } from '~/utils/clipboard'

// MCP（Model Context Protocol）接入卡：免登录公开只读，或在账号中心切到个人专属令牌模式。
const props = withDefaults(defineProps<{ compact?: boolean, personal?: boolean }>(), { compact: false, personal: false })
const { user } = useAuth()
const scope = ref<'personal' | 'public'>(props.personal ? 'personal' : 'public')
const clientTab = ref<'json' | 'cli'>('json')
const origin = ref('')
const tokenData = ref<{ token: string, mcp_path?: string } | null>(null)
const tokenLoading = ref(false)
const tokenBusy = ref(false)
const tokenError = ref('')

async function loadToken() {
  if (!props.personal || !user.value) return
  tokenLoading.value = true
  tokenError.value = ''
  try {
    tokenData.value = await $fetch<{ token: string, mcp_path?: string }>('/api/account/feed-token')
  } catch (exc) {
    if (apiErrorStatus(exc) === 401) user.value = null
    tokenError.value = apiErrorDetail(exc, '个人 MCP 令牌加载失败，请重试。')
  } finally {
    tokenLoading.value = false
  }
}

async function rotateToken() {
  if (tokenBusy.value) return
  if (!confirm('重置个人令牌？旧的个人 RSS 与个人 MCP 地址都会立即失效，需要在客户端中更新。')) return
  tokenBusy.value = true
  tokenError.value = ''
  try {
    tokenData.value = await $fetch<{ token: string, mcp_path?: string }>('/api/account/feed-token/rotate', { method: 'POST' })
  } catch (exc) {
    if (apiErrorStatus(exc) === 401) user.value = null
    tokenError.value = apiErrorDetail(exc, '重置失败，请稍后重试。')
  } finally {
    tokenBusy.value = false
  }
}

onMounted(() => {
  origin.value = window.location.origin
  if (props.personal) loadToken()
})

const isPersonal = computed(() => scope.value === 'personal' && props.personal)
const baseEndpoint = computed(() => (origin.value ? `${origin.value}/mcp` : '/mcp'))
const endpointUrl = computed(() => {
  if (isPersonal.value) {
    if (!tokenData.value) return ''
    return `${origin.value}${tokenData.value.mcp_path || `/mcp?token=${tokenData.value.token}`}`
  }
  return baseEndpoint.value
})

const snippet = computed(() => {
  const url = baseEndpoint.value
  const token = isPersonal.value ? tokenData.value?.token : ''
  if (clientTab.value === 'cli') {
    if (isPersonal.value && token) {
      return `claude mcp add --transport http anisignal ${url} --header "Authorization: Bearer ${token}"`
    }
    return `claude mcp add --transport http anisignal ${url}`
  }
  const serverCfg: Record<string, unknown> = { url }
  if (isPersonal.value && token) {
    serverCfg.headers = { Authorization: `Bearer ${token}` }
  }
  return JSON.stringify({ mcpServers: { anisignal: serverCfg } }, null, 2)
})

const capabilities = computed(() => [
  '全站检索', '活动日历', '可用兑换码', '官方暗改 Diff', '聚合事件', '周边履约', 'Steam 遥测', 'AI 日报',
  ...(isPersonal.value ? ['我的关注流'] : []),
])

const copiedKind = ref<'' | 'url' | 'snippet'>('')
let timer: ReturnType<typeof setTimeout> | undefined
async function copyValue(kind: 'url' | 'snippet', value: string) {
  if (!value) return
  if (await copyText(value)) {
    copiedKind.value = kind
    clearTimeout(timer)
    timer = setTimeout(() => { copiedKind.value = '' }, 2000)
  }
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section id="mcp-access" class="panel scroll-mt-24 p-5" aria-labelledby="mcp-title">
    <header class="flex items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-ink" aria-hidden="true"><Cpu :size="19" /></span>
      <div class="min-w-0 flex-1">
        <h2 id="mcp-title" class="text-base font-bold text-ink">AI 助手接入（MCP）</h2>
        <p class="mt-0.5 text-xs leading-relaxed text-muted">
          {{ isPersonal ? '按你的分区偏好与屏蔽词过滤，并解锁个人关注流与定制监控（与个人 RSS 共用令牌）。' : '免登录直连 Claude / Cursor / Antigravity / Cherry Studio 等支持 Streamable HTTP 的 AI 客户端。' }}
        </p>
      </div>
      <div v-if="personal" class="flex shrink-0 rounded-lg bg-chip p-0.5 text-xs" role="group" aria-label="MCP 接入模式">
        <button
          type="button"
          class="rounded-md px-2 py-1 transition-colors"
          :class="scope === 'personal' ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
          @click="scope = 'personal'"
        >
          个人
        </button>
        <button
          type="button"
          class="rounded-md px-2 py-1 transition-colors"
          :class="scope === 'public' ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
          @click="scope = 'public'"
        >
          公开
        </button>
      </div>
    </header>

    <UiNotice v-if="tokenError && isPersonal" tone="error" class="mt-3">
      {{ tokenError }}<button type="button" class="ml-2 font-semibold underline" @click="loadToken">重试</button>
    </UiNotice>

    <div class="mt-4 flex gap-2">
      <input
        :value="tokenLoading && isPersonal ? '正在生成专属 MCP 地址…' : endpointUrl"
        readonly
        aria-label="MCP 地址"
        class="input mono h-10 min-w-0 flex-1 text-xs"
        @focus="($event.target as HTMLInputElement).select()"
      >
      <button
        type="button"
        class="inline-flex h-10 shrink-0 items-center gap-1 rounded-[10px] border border-line px-3 text-xs font-semibold text-brand-ink hover:bg-brand-soft disabled:opacity-40"
        :disabled="!endpointUrl"
        @click="copyValue('url', endpointUrl)"
      >
        <Check v-if="copiedKind === 'url'" :size="14" /><Copy v-else :size="14" />{{ copiedKind === 'url' ? '已复制' : '复制地址' }}
      </button>
    </div>

    <div class="mt-3 flex flex-wrap gap-1.5">
      <span
        v-for="item in capabilities"
        :key="item"
        class="rounded-md bg-chip px-2 py-0.5 text-[11px] text-ink-2"
      >
        {{ item }}
      </span>
    </div>

    <details class="mt-3 rounded-xl border border-line-soft bg-chip/35 px-3.5 py-2.5 text-xs">
      <summary class="cursor-pointer select-none font-semibold text-ink-2">客户端配置示例（JSON / Claude Code）</summary>
      <div class="mt-2.5 flex items-center gap-2">
        <div class="flex rounded-lg bg-chip p-0.5" role="group" aria-label="配置格式">
          <button
            type="button"
            class="rounded-md px-2 py-1 transition-colors"
            :class="clientTab === 'json' ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
            @click="clientTab = 'json'"
          >
            通用 JSON
          </button>
          <button
            type="button"
            class="rounded-md px-2 py-1 transition-colors"
            :class="clientTab === 'cli' ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
            @click="clientTab = 'cli'"
          >
            Claude Code
          </button>
        </div>
        <button
          type="button"
          class="ml-auto inline-flex h-7 items-center gap-1 font-semibold text-brand-ink hover:underline disabled:opacity-40"
          :disabled="isPersonal && !tokenData"
          @click="copyValue('snippet', snippet)"
        >
          <Check v-if="copiedKind === 'snippet'" :size="13" /><Copy v-else :size="13" />{{ copiedKind === 'snippet' ? '已复制' : '复制配置' }}
        </button>
      </div>
      <pre class="mono mt-2 overflow-x-auto rounded-lg bg-canvas/80 p-2.5 text-[11px] leading-relaxed text-ink-2">{{ snippet }}</pre>
    </details>

    <div class="mt-2 flex flex-wrap items-center gap-4 text-[12px] text-muted">
      <span>Streamable HTTP · 严格只读</span>
      <a href="/mcp" target="_blank" rel="noopener" class="hover:text-ink">查看接口描述</a>
      <button
        v-if="isPersonal && tokenData"
        type="button"
        class="ml-auto inline-flex h-7 items-center gap-1 text-xs text-muted hover:text-ink disabled:opacity-40"
        :disabled="tokenBusy"
        @click="rotateToken"
      >
        <RefreshCw :size="12" :class="{ 'animate-spin': tokenBusy }" aria-hidden="true" />
        {{ tokenBusy ? '重置中…' : '重置令牌' }}
      </button>
    </div>
  </section>
</template>
