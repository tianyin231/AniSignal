<script setup lang="ts">
import { ArrowLeft, KeyRound } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'

definePageMeta({ layout: 'auth' })
useSeoMeta({ title: '找回密码 · AniSignal' })

const { withRedirect } = useAuthRedirect()
const flash = useAuthFlash()
const step = ref<'email' | 'reset'>('email')
const email = ref(flash.value?.email ?? '')
flash.value = null
const code = ref('')
const newPassword = ref('')
const error = ref('')
const busy = ref(false)
const { remaining, start } = useCooldown()

async function sendCode() {
  if (busy.value || !email.value.trim() || remaining.value > 0) return
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/password/forgot', { method: 'POST', body: { email: email.value.trim() } })
    step.value = 'reset'
    start()
  } catch (exc) {
    error.value = apiErrorDetail(exc, '发送失败，请稍后再试')
    if (apiErrorStatus(exc) === 429) start()
  } finally {
    busy.value = false
  }
}

async function submitReset() {
  if (busy.value || code.value.length !== 6 || newPassword.value.length < 8) return
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/password/reset', {
      method: 'POST',
      body: { email: email.value.trim(), code: code.value, new_password: newPassword.value },
    })
    flash.value = { email: email.value.trim(), message: '密码已重置，所有设备已退出，请用新密码登录。' }
    await navigateTo(withRedirect('/login'), { replace: true })
  } catch (exc) {
    error.value = apiErrorDetail(exc, '重置失败，请重试')
    busy.value = false
  }
}

function changeEmail() {
  step.value = 'email'
  code.value = newPassword.value = error.value = ''
}
</script>

<template>
  <form v-if="step === 'email'" novalidate @submit.prevent="sendCode">
    <NuxtLink :to="withRedirect('/login')" class="-ml-1 inline-flex h-8 items-center gap-1 rounded-lg px-1 text-sm text-muted transition-colors hover:text-ink">
      <ArrowLeft :size="15" /> 返回登录
    </NuxtLink>
    <span class="mt-4 grid size-11 place-items-center rounded-xl bg-brand-soft text-brand-ink" aria-hidden="true">
      <KeyRound :size="21" />
    </span>
    <h1 class="mt-4 text-2xl font-bold text-ink">找回密码</h1>
    <p class="mt-1.5 text-sm leading-relaxed text-muted">输入注册邮箱，我们会发送 6 位重置码，15 分钟内有效。</p>

    <label for="email" class="mt-6 block text-sm font-medium text-ink-2">邮箱</label>
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

    <UiNotice v-if="error" tone="error" class="mt-4">{{ error }}</UiNotice>

    <button type="submit" class="btn-primary mt-5 h-11 w-full" :disabled="busy || !email.trim() || remaining > 0">
      {{ busy ? '发送中…' : remaining > 0 ? `${remaining} 秒后可重新发送` : '发送重置码' }}
    </button>
    <p class="mt-4 text-center text-xs leading-relaxed text-faint">出于安全考虑，无论邮箱是否注册，提示都相同。</p>
  </form>

  <form v-else novalidate @submit.prevent="submitReset">
    <button type="button" class="-ml-1 inline-flex h-8 items-center gap-1 rounded-lg px-1 text-sm text-muted transition-colors hover:text-ink" @click="changeEmail">
      <ArrowLeft :size="15" /> 换个邮箱
    </button>
    <h1 class="mt-4 text-2xl font-bold text-ink">设置新密码</h1>
    <p class="mt-1.5 text-sm leading-relaxed text-muted">
      如果 <span class="font-medium break-all text-ink-2">{{ email.trim() }}</span> 已注册，重置码已经发出，15 分钟内有效。
    </p>

    <AuthCodeInput v-model="code" label="6 位重置码" class="mt-6" autofocus />
    <div class="mt-4">
      <AuthPasswordField id="new-password" v-model="newPassword" label="新密码" autocomplete="new-password" hint="至少 8 位；重置后所有设备会退出登录" />
    </div>

    <UiNotice v-if="error" tone="error" class="mt-1">{{ error }}</UiNotice>

    <button type="submit" class="btn-primary mt-5 h-11 w-full" :disabled="busy || code.length !== 6 || newPassword.length < 8">
      {{ busy ? '重置中…' : '重置密码' }}
    </button>
    <p class="mt-4 text-center text-sm text-muted">
      没收到？
      <button type="button" class="link font-medium disabled:text-faint disabled:no-underline" :disabled="remaining > 0 || busy" @click="sendCode">
        {{ remaining > 0 ? `${remaining} 秒后可重新发送` : '重新发送' }}
      </button>
    </p>
  </form>
</template>
