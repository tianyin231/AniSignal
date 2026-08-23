<script setup lang="ts">
useSeoMeta({ title: '注册 · AniSignal' })

const step = ref<'form' | 'code'>('form')
const email = ref('')
const password = ref('')
const confirm = ref('')
const code = ref('')
const error = ref('')
const info = ref('')
const busy = ref(false)
const { login, fetchMe } = useAuth()

async function submit() {
  if (busy.value) return
  if (password.value !== confirm.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/register', {
      method: 'POST',
      body: { email: email.value.trim(), password: password.value },
    })
    info.value = '验证码已发送，请查收邮箱（含垃圾邮件箱）'
    step.value = 'code'
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '注册失败，请稍后再试'
  } finally {
    busy.value = false
  }
}

async function resend() {
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/resend', { method: 'POST', body: { email: email.value.trim() } })
    info.value = '已重新发送验证码'
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '发送失败'
  } finally {
    busy.value = false
  }
}

async function verify() {
  if (busy.value || code.value.trim().length !== 6) return
  busy.value = true
  error.value = ''
  try {
    await $fetch('/api/auth/verify', {
      method: 'POST',
      body: { email: email.value.trim(), code: code.value.trim() },
    })
    await fetchMe()
    navigateTo('/account')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '验证失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-sm py-10">
    <form v-if="step === 'form'" class="panel p-6" @submit.prevent="submit">
      <h1 class="text-lg font-semibold">注册</h1>
      <p class="mt-1 text-sm text-slate-500 dark:text-zinc-400">注册免费，用于订阅与通知；全站内容不登录也可浏览。</p>

      <label class="mt-5 block text-sm font-medium" for="email">邮箱</label>
      <input id="email" v-model="email" type="email" autocomplete="email" class="mt-1.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">

      <label class="mt-4 block text-sm font-medium" for="password">密码（至少 8 位）</label>
      <input id="password" v-model="password" type="password" autocomplete="new-password" class="mt-1.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">

      <label class="mt-4 block text-sm font-medium" for="confirm">确认密码</label>
      <input id="confirm" v-model="confirm" type="password" autocomplete="new-password" class="mt-1.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">

      <p v-if="error" class="mt-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <button type="submit" class="btn-primary mt-6 w-full px-4 py-2" :disabled="busy || !email.trim() || !password">
        {{ busy ? '提交中…' : '发送验证码' }}
      </button>

      <p class="mt-4 text-center text-xs">
        已有账号？<NuxtLink to="/login" class="link">直接登录</NuxtLink>
      </p>
    </form>

    <form v-else class="panel p-6" @submit.prevent="verify">
      <h1 class="text-lg font-semibold">验证邮箱</h1>
      <p class="mt-1 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
        {{ info }}<br>验证码已发至 <span class="mono">{{ email }}</span>，15 分钟内有效。
      </p>

      <input
        v-model="code"
        inputmode="numeric"
        maxlength="6"
        placeholder="6 位验证码"
        class="mono mt-5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-center text-lg tracking-[0.4em] dark:border-white/15 dark:bg-white/5"
      >

      <p v-if="error" class="mt-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <button type="submit" class="btn-primary mt-5 w-full px-4 py-2" :disabled="busy || code.trim().length !== 6">
        {{ busy ? '验证中…' : '完成验证并登录' }}
      </button>
      <button type="button" class="mt-2 w-full px-4 py-2 text-xs text-slate-400 hover:underline dark:text-zinc-500" :disabled="busy" @click="resend">
        没收到？重新发送
      </button>
    </form>
  </div>
</template>
