<script setup lang="ts">
definePageMeta({ layout: 'auth' })
useSeoMeta({ title: '找回密码 · AniSignal' })

const step = ref<'email' | 'reset'>('email')
const email = ref('')
const code = ref('')
const newPassword = ref('')
const error = ref('')
const busy = ref(false)

async function submitEmail() {
  if (busy.value || !email.value.trim()) return
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/password/forgot', { method: 'POST', body: { email: email.value.trim() } })
    step.value = 'reset'
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '发送失败，请稍后再试'
  } finally {
    busy.value = false
  }
}

async function submitReset() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/password/reset', {
      method: 'POST',
      body: { email: email.value.trim(), code: code.value.trim(), new_password: newPassword.value },
    })
    navigateTo('/login')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '重置失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="w-full">
    <form v-if="step === 'email'" class="panel p-6" @submit.prevent="submitEmail">
      <h1 class="text-lg font-semibold">找回密码</h1>
      <p class="mt-1 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
        输入注册邮箱，我们将发送 6 位重置码（15 分钟内有效）。
      </p>

      <label class="mt-5 block text-sm font-medium" for="email">邮箱</label>
      <input id="email" v-model="email" type="email" autocomplete="email" class="mt-1.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">

      <p v-if="error" class="mt-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <button type="submit" class="btn-primary mt-6 w-full px-4 py-2" :disabled="busy">
        {{ busy ? '发送中…' : '发送重置码' }}
      </button>
      <p class="mt-4 text-center text-xs text-slate-400 dark:text-zinc-500">
        出于安全考虑，无论邮箱是否注册，提示都相同。
      </p>
    </form>

    <form v-else class="panel p-6" @submit.prevent="submitReset">
      <h1 class="text-lg font-semibold">设置新密码</h1>
      <p class="mt-1 text-sm text-slate-500 dark:text-zinc-400">重置码已发至 <span class="mono">{{ email }}</span></p>

      <input v-model="code" inputmode="numeric" maxlength="6" placeholder="6 位重置码"
        class="mono mt-5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-center text-lg tracking-[0.4em] dark:border-white/15 dark:bg-white/5">
      <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="新密码（至少 8 位）"
        class="mt-3 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">

      <p v-if="error" class="mt-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <button type="submit" class="btn-primary mt-5 w-full px-4 py-2" :disabled="busy || code.length !== 6 || !newPassword">
        {{ busy ? '重置中…' : '重置密码并去登录' }}
      </button>
      <p class="mt-3 text-center text-xs text-slate-400 dark:text-zinc-500">重置后所有已登录设备将退出。</p>
    </form>
  </div>
</template>
