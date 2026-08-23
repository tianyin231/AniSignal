<script setup lang="ts">
import { fmtTime } from '~/utils/public-utils'

definePageMeta({ layout: 'auth' })
useSeoMeta({ title: '账号设置 · AniSignal' })

const { user, loaded, fetchMe, logout } = useAuth()
const oldPassword = ref('')
const newPassword = ref('')
const message = ref('')
const error = ref('')
const busy = ref(false)

onMounted(() => {
  // 未登录跳登录页（等 me 拉取完成再判断）
  watchEffect(() => {
    if (loaded.value && !user.value) navigateTo('/login', { replace: true })
  })
})

async function changePassword() {
  if (busy.value) return
  busy.value = true
  error.value = message.value = ''
  try {
    await $fetch('/api/auth/password/change', {
      method: 'POST',
      body: { old_password: oldPassword.value, new_password: newPassword.value },
    })
    message.value = '密码已修改；其他设备的登录已全部退出。'
    oldPassword.value = newPassword.value = ''
    await fetchMe()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '修改失败'
  } finally {
    busy.value = false
  }
}

async function handleLogout() {
  await logout()
  navigateTo('/')
}
</script>

<template>
  <div class="w-full space-y-4">
    <section v-if="user" class="panel p-5">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-lg font-semibold">{{ user.email }}</h1>
          <p class="mono mt-1 text-xs text-slate-400 dark:text-zinc-500">
            注册于 {{ fmtTime(user.created_at) }} · 最近登录 {{ fmtTime(user.last_login_at) }}
          </p>
        </div>
        <span class="chip bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">已验证</span>
      </div>
    </section>

    <section class="panel p-5">
      <h2 class="text-sm font-semibold">修改密码</h2>
      <input v-model="oldPassword" type="password" autocomplete="current-password" placeholder="原密码"
        class="mt-3 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
      <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="新密码（至少 8 位）"
        class="mt-2.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
      <p v-if="error" class="mt-2 text-sm text-red-600 dark:text-red-400">{{ error }}</p>
      <p v-if="message" class="mt-2 text-sm text-emerald-600 dark:text-emerald-400">{{ message }}</p>
      <div class="mt-4 flex justify-between">
        <button class="btn-primary px-4 py-2" :disabled="busy || !oldPassword || !newPassword" @click="changePassword">
          {{ busy ? '提交中…' : '修改密码' }}
        </button>
        <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" @click="handleLogout">
          退出登录
        </button>
      </div>
    </section>

    <section class="panel p-5">
      <h2 class="text-sm font-semibold">订阅与监控位</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
        账号已就绪。邮件早报与监控位功能上线后将在此配置；届时你的邮箱将用于接收通知。
        可先到 <NuxtLink to="/watch" class="link">订阅中心</NuxtLink> 了解规划。
      </p>
    </section>
  </div>
</template>
