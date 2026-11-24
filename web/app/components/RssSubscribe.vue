<script setup lang="ts">
import { Check, Copy, RefreshCw, Rss } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'
import { copyText } from '~/utils/clipboard'

// RSS 订阅：公开分区无需登录；账号中心内支持切换个人专属过滤源（带可重置令牌）。
const props = withDefaults(defineProps<{ compact?: boolean, personal?: boolean }>(), { compact: false, personal: false })
const { communities } = useGameSection()
const { user } = useAuth()
const scope = ref<'personal' | 'public'>(props.personal ? 'personal' : 'public')
const community = ref('all')
const personalMode = ref<'all' | 'keywords'>('all')
const origin = ref('')
const tokenData = ref<{ token: string, all_path: string, keywords_path: string } | null>(null)
const tokenLoading = ref(false)
const tokenBusy = ref(false)
const tokenError = ref('')

async function loadToken() {
  if (!props.personal || !user.value) return
  tokenLoading.value = true
  tokenError.value = ''
  try {
    tokenData.value = await $fetch<{ token: string, all_path: string, keywords_path: string }>('/api/account/feed-token')
  } catch (exc) {
    if (apiErrorStatus(exc) === 401) user.value = null
    tokenError.value = apiErrorDetail(exc, '个人 RSS 令牌加载失败，请重试。')
  } finally {
    tokenLoading.value = false
  }
}

async function rotateToken() {
  if (tokenBusy.value) return
  if (!confirm('重置个人 RSS 地址？旧地址会立即失效，需要在阅读器中更新。')) return
  tokenBusy.value = true
  tokenError.value = ''
  try {
    tokenData.value = await $fetch<{ token: string, all_path: string, keywords_path: string }>('/api/account/feed-token/rotate', { method: 'POST' })
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

const path = computed(() => {
  if (scope.value === 'personal' && props.personal) {
    if (!tokenData.value) return ''
    return personalMode.value === 'keywords' ? tokenData.value.keywords_path : tokenData.value.all_path
  }
  return `/rss.xml?community=${community.value}`
})
const url = computed(() => (path.value ? `${origin.value}${path.value}` : ''))
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
async function copy() {
  if (!url.value) return
  if (await copyText(url.value)) {
    copied.value = true
    clearTimeout(timer)
    timer = setTimeout(() => { copied.value = false }, 2000)
  } else {
    copied.value = false
  }
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section class="panel p-5" aria-labelledby="rss-title">
    <header class="flex items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-warn/10 text-warn" aria-hidden="true"><Rss :size="19" /></span>
      <div class="min-w-0 flex-1">
        <h2 id="rss-title" class="text-base font-bold text-ink">RSS 订阅</h2>
        <p class="mt-0.5 text-xs leading-relaxed text-muted">
          {{ scope === 'personal' && personal ? '按你的分区偏好、屏蔽规则与关注词生成的专属订阅源。' : '把站内动态放进你常用的阅读器，不需要登录。' }}
        </p>
      </div>
      <div v-if="personal" class="flex shrink-0 rounded-lg bg-chip p-0.5 text-xs" role="group" aria-label="RSS 模式">
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

    <UiNotice v-if="tokenError && scope === 'personal'" tone="error" class="mt-3">
      {{ tokenError }}<button type="button" class="ml-2 font-semibold underline" @click="loadToken">重试</button>
    </UiNotice>

    <div class="mt-4 flex flex-wrap gap-2" :class="compact ? '' : 'sm:flex-nowrap'">
      <select v-if="scope === 'personal' && personal" v-model="personalMode" aria-label="个人 RSS 过滤模式" class="select h-10 shrink-0">
        <option value="all">个人偏好过滤</option>
        <option value="keywords">仅命中关键词</option>
      </select>
      <select v-else v-model="community" aria-label="RSS 范围" class="select h-10 shrink-0">
        <option value="all">全部分区</option>
        <option v-for="c in communities" :key="c.key" :value="c.key">{{ c.key === 'ai' ? 'AI 快讯' : c.label }}</option>
      </select>
      <input
        :value="tokenLoading && scope === 'personal' ? '正在生成专属地址…' : (url || path)"
        readonly
        aria-label="RSS 地址"
        class="input mono h-10 min-w-0 flex-1 text-xs"
        @focus="($event.target as HTMLInputElement).select()"
      >
    </div>

    <div class="mt-2 flex flex-wrap items-center gap-4 text-[13px]">
      <button type="button" class="inline-flex h-8 items-center gap-1 font-semibold text-brand-ink disabled:opacity-40" :disabled="!path" @click="copy">
        <Check v-if="copied" :size="14" /><Copy v-else :size="14" />{{ copied ? '已复制' : '复制地址' }}
      </button>
      <a v-if="path" :href="path" target="_blank" rel="noopener" class="inline-flex h-8 items-center text-muted hover:text-ink">在新标签打开</a>
      <button
        v-if="scope === 'personal' && personal && tokenData"
        type="button"
        class="ml-auto inline-flex h-8 items-center gap-1 text-xs text-muted hover:text-ink disabled:opacity-40"
        :disabled="tokenBusy"
        @click="rotateToken"
      >
        <RefreshCw :size="12" :class="{ 'animate-spin': tokenBusy }" aria-hidden="true" />
        {{ tokenBusy ? '重置中…' : '重置地址' }}
      </button>
    </div>
  </section>
</template>
