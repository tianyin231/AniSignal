<script setup lang="ts">
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'

definePageMeta({ layout: 'auth' })
useSeoMeta({ title: '登录 · AniSignal' })

// 登录成功后由 auth 布局跳回来源页（?redirect=），这里只负责表单本身。
const { login } = useAuth()
const { withRedirect } = useAuthRedirect()
const flash = useAuthFlash()
const email = ref(flash.value?.email ?? '')
const notice = ref(flash.value?.message ?? '')
flash.value = null

const password = ref('')
const error = ref('')
const busy = ref(false)
const unverified = ref(false)
const verifying = ref(false)

async function submit() {
  const address = email.value.trim()
  if (busy.value || !address || !password.value) return
  busy.value = true
  error.value = notice.value = ''
  unverified.value = false
  try {
    await login(address, password.value)
  } catch (exc) {
    const detail = apiErrorDetail(exc, '登录失败，请稍后再试')
    unverified.value = apiErrorStatus(exc) === 403 && detail.includes('未验证')
    error.value = unverified.value ? '这个邮箱还没有完成验证，验证后即可登录。' : detail
    busy.value = false
  }
}

function rememberEmail() {
  if (email.value.trim()) flash.value = { email: email.value.trim() }
}
</script>

<template>
  <AuthVerifyEmail
    v-if="verifying"
    :email="email.trim()"
    auto-send
    heading="验证邮箱后继续"
    @back="verifying = false"
  />
  <form v-else novalidate @submit.prevent="submit">
    <AuthModeSwitch active="login" :email="email" />
    <h1 class="mt-7 text-2xl font-bold text-ink">欢迎回来</h1>
    <p class="mt-1.5 text-sm text-muted">登录后管理关注、通知与定制监控。</p>

    <UiNotice v-if="notice" tone="ok" class="mt-5">{{ notice }}</UiNotice>

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
          class="input mt-1.5 h-11 w-full"
        >
      </div>
      <AuthPasswordField id="password" v-model="password" label="密码" autocomplete="current-password">
        <template #aside>
          <NuxtLink :to="withRedirect('/forgot')" class="link text-xs" @click="rememberEmail">忘记密码？</NuxtLink>
        </template>
      </AuthPasswordField>
    </div>

    <UiNotice v-if="error" tone="error" class="mt-1">
      {{ error }}
      <button v-if="unverified" type="button" class="mt-1 block font-semibold underline underline-offset-2" @click="verifying = true">
        发送验证码，完成验证
      </button>
    </UiNotice>

    <button type="submit" class="btn-primary mt-5 h-11 w-full" :disabled="busy || !email.trim() || !password">
      {{ busy ? '登录中…' : '登录' }}
    </button>
    <p class="mt-5 text-center text-xs text-faint">浏览全站内容无需登录</p>
  </form>
</template>
