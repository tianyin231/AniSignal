<script setup lang="ts">
import { ArrowLeft, MailCheck } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'

// 邮箱验证步骤：注册后填码，或"邮箱未验证"的账号登录时就地补验证。
// 验证成功即登录（后端写会话 cookie），随后由 auth 布局跳回来源页。
const props = withDefaults(defineProps<{ email: string, autoSend?: boolean, heading?: string }>(), {
  autoSend: false,
  heading: '验证邮箱',
})
const emit = defineEmits<{ back: [] }>()
const { fetchMe } = useAuth()
const { remaining, start } = useCooldown()
const code = ref('')
const error = ref('')
const sending = ref(false)
const verifying = ref(false)

async function send() {
  if (remaining.value > 0 || sending.value) return
  sending.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/resend', { method: 'POST', body: { email: props.email } })
    start()
  } catch (exc) {
    error.value = apiErrorDetail(exc, '发送失败，请稍后再试')
    if (apiErrorStatus(exc) === 429) start()
  } finally {
    sending.value = false
  }
}

async function verify() {
  if (verifying.value || code.value.length !== 6) return
  verifying.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/verify', { method: 'POST', body: { email: props.email, code: code.value } })
    await fetchMe()
  } catch (exc) {
    error.value = apiErrorDetail(exc, '验证失败，请重试')
    code.value = ''
    verifying.value = false
  }
}

onMounted(() => (props.autoSend ? send() : start()))
</script>

<template>
  <form novalidate @submit.prevent="verify">
    <button type="button" class="-ml-1 inline-flex h-8 items-center gap-1 rounded-lg px-1 text-sm text-muted transition-colors hover:text-ink" @click="emit('back')">
      <ArrowLeft :size="15" /> 返回
    </button>
    <span class="mt-4 grid size-11 place-items-center rounded-xl bg-brand-soft text-brand-ink" aria-hidden="true">
      <MailCheck :size="22" />
    </span>
    <h1 class="mt-4 text-2xl font-bold text-ink">{{ heading }}</h1>
    <p class="mt-1.5 text-sm leading-relaxed text-muted">
      输入发送到 <span class="font-medium break-all text-ink-2">{{ email }}</span> 的 6 位验证码，15 分钟内有效。
    </p>
    <slot />

    <AuthCodeInput v-model="code" class="mt-6" autofocus @complete="verify" />
    <UiNotice v-if="error" tone="error" class="mt-4">{{ error }}</UiNotice>

    <button type="submit" class="btn-primary mt-5 h-11 w-full" :disabled="verifying || code.length !== 6">
      {{ verifying ? '验证中…' : '验证并登录' }}
    </button>
    <p class="mt-4 text-center text-sm text-muted">
      没收到？
      <button type="button" class="link font-medium disabled:text-faint disabled:no-underline" :disabled="remaining > 0 || sending" @click="send">
        {{ sending ? '发送中…' : remaining > 0 ? `${remaining} 秒后可重新发送` : '重新发送' }}
      </button>
    </p>
    <p class="mt-1 text-center text-xs text-faint">也看看垃圾邮件箱。</p>
  </form>
</template>
