<script setup lang="ts">
definePageMeta({ layout: 'auth' })
useSeoMeta({ title: '登录 · AniSignal' })

const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const { login } = useAuth()
const route = useRoute()
const redirectTarget = () => {
  const target = String(route.query.redirect || '/')
  // 只允许站内路径，防开放跳转
  return target.startsWith('/') && !target.startsWith('//') ? target : '/account'
}

async function submit() {
  if (!email.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await login(email.value.trim(), password.value)
    navigateTo(redirectTarget())
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '登录失败，请稍后再试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="w-full">
    <form class="panel p-6" @submit.prevent="submit">
      <h1 class="text-lg font-semibold">登录</h1>
      <p class="mt-1 text-sm text-slate-500 dark:text-zinc-400">全站内容免费浏览；登录用于管理订阅与通知。</p>

      <label class="mt-5 block text-sm font-medium" for="email">邮箱</label>
      <input id="email" v-model="email" type="email" autocomplete="email" class="input mt-1.5 w-full px-3 py-2 text-sm">

      <label class="mt-4 block text-sm font-medium" for="password">密码</label>
      <input id="password" v-model="password" type="password" autocomplete="current-password" class="input mt-1.5 w-full px-3 py-2 text-sm">

      <p v-if="error" class="mt-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

      <button type="submit" class="btn-primary mt-6 w-full px-4 py-2" :disabled="busy || !password">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <div class="mt-4 flex justify-between text-xs">
        <NuxtLink to="/register" class="link">注册新账号</NuxtLink>
        <NuxtLink to="/forgot" class="link">忘记密码？</NuxtLink>
      </div>
    </form>
  </div>
</template>
