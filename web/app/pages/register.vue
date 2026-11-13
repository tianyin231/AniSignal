<script setup lang="ts">
import { apiErrorDetail } from '~/utils/api-errors'

definePageMeta({ layout: 'auth' })
useSeoMeta({ title: '注册 · AniSignal' })

const flash = useAuthFlash()
const { smtpConfigured } = useSiteConfig()
const step = ref<'form' | 'code'>('form')
const email = ref(flash.value?.email ?? '')
flash.value = null
const password = ref('')
const confirm = ref('')
const error = ref('')
const busy = ref(false)
const attempted = ref(false)

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const emailInvalid = computed(() => attempted.value && !EMAIL_PATTERN.test(email.value.trim()))
const passwordInvalid = computed(() => attempted.value && password.value.length < 8)
// 确认框还没输完时不提示，提交后或长度追上密码后再判断
const confirmInvalid = computed(() => confirm.value !== password.value
  && (attempted.value || (confirm.value.length > 0 && confirm.value.length >= password.value.length)))

async function submit() {
  if (busy.value) return
  attempted.value = true
  error.value = ''
  if (emailInvalid.value || passwordInvalid.value || confirm.value !== password.value) return
  busy.value = true
  try {
    await $fetch('/api/auth/register', { method: 'POST', body: { email: email.value.trim(), password: password.value } })
    step.value = 'code'
  } catch (exc) {
    error.value = apiErrorDetail(exc, '注册失败，请稍后再试')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <AuthVerifyEmail v-if="step === 'code'" :email="email.trim()" @back="step = 'form'">
    <UiNotice v-if="!smtpConfigured" tone="warn" class="mt-3">
      当前站点尚未配置邮件发送（SMTP）。请联系管理员在「管理看板 → 用户管理」查看 6 位注册验证码或直接激活账号。
    </UiNotice>
    <p class="mt-2 text-xs leading-relaxed text-faint">如果这个邮箱已经注册过，不会再收到验证码，请直接登录或找回密码。</p>
  </AuthVerifyEmail>
  <form v-else novalidate @submit.prevent="submit">
    <AuthModeSwitch active="register" :email="email" />
    <h1 class="mt-7 text-2xl font-bold text-ink">创建账号</h1>
    <p class="mt-1.5 text-sm text-muted">免费注册，用来保存关注词与通知方式。</p>
    <UiNotice v-if="!smtpConfigured" tone="info" class="mt-4">
      当前站点尚未配置 SMTP 发信；提交注册后验证码将保存在后台，可由管理员协助验证或一键激活。
    </UiNotice>

    <div class="mt-6 space-y-3">
      <div>
        <label for="email" class="text-sm font-medium text-ink-2">邮箱</label>
        <input
          id="email"
          v-model="email"
          type="email"
          inputmode="email"
          autocomplete="email"
          autocapitalize="off"
          spellcheck="false"
          placeholder="you@example.com"
          :aria-invalid="emailInvalid || undefined"
          aria-describedby="email-hint"
          class="input mt-1.5 h-11 w-full"
          :class="emailInvalid ? 'border-hot!' : ''"
        >
        <p id="email-hint" class="mt-1.5 min-h-4 text-xs" :class="emailInvalid ? 'text-hot' : 'text-faint'">
          {{ emailInvalid ? '请输入有效的邮箱地址' : '验证码会发到这个邮箱' }}
        </p>
      </div>
      <AuthPasswordField
        id="password"
        v-model="password"
        label="密码"
        autocomplete="new-password"
        :invalid="passwordInvalid"
        :hint="passwordInvalid ? '密码至少 8 位' : '至少 8 位'"
      />
      <AuthPasswordField
        id="confirm"
        v-model="confirm"
        label="确认密码"
        autocomplete="new-password"
        :invalid="confirmInvalid"
        :hint="confirmInvalid ? '两次输入的密码不一致' : ''"
      />
    </div>

    <UiNotice v-if="error" tone="error" class="mt-1">{{ error }}</UiNotice>

    <button type="submit" class="btn-primary mt-5 h-11 w-full" :disabled="busy">
      {{ busy ? '提交中…' : '发送验证码' }}
    </button>
    <p class="mt-4 text-center text-xs leading-relaxed text-faint">
      注册即表示同意<NuxtLink to="/terms" class="link">用户条款</NuxtLink>与<NuxtLink to="/privacy" class="link">隐私政策</NuxtLink>
    </p>
  </form>
</template>
