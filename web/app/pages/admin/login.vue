<script setup lang="ts">
import { loginRoute } from '~/utils/safe-redirect'

definePageMeta({ layout: 'admin' })
useSeoMeta({ title: '登录 · AniSignal 管理看板' })

const { login } = useAdminApi()
const { user, fetchMe } = useAuth()
const tokenInput = ref('')
const bindSelf = ref(true)
const checking = ref(false)
const failed = ref(false)

onMounted(async () => {
  await fetchMe()
  if (user.value?.is_admin) {
    navigateTo('/admin', { replace: true })
  }
})

async function submit() {
  if (!tokenInput.value.trim() || checking.value) return
  checking.value = true
  failed.value = false
  const shouldBind = Boolean(user.value && bindSelf.value)
  const ok = await login(tokenInput.value.trim(), shouldBind)
  checking.value = false
  if (ok) navigateTo('/admin', { replace: true })
  else failed.value = true
}
</script>

<template>
  <div class="flex min-h-[60vh] items-center justify-center">
    <form class="panel w-full max-w-sm p-8" @submit.prevent="submit">
      <h1 class="text-lg font-semibold">管理员登录</h1>
      <p class="mt-1 text-sm text-muted">
        输入后端配置的管理员 Token（<span class="mono">ANISIGNAL_ADMIN_TOKEN</span>）。绑定个人站点账号后，日常登录账号即可直进后台。
      </p>
      <label class="mt-6 block text-sm font-medium" for="token">Token</label>
      <input
        id="token"
        v-model="tokenInput"
        type="password"
        autocomplete="current-password"
        placeholder="admin token"
        class="input mono mt-1.5 w-full px-3 py-2 text-sm focus:border-blue-500"
      />
      <label v-if="user" class="mt-4 flex cursor-pointer items-start gap-2 rounded-lg bg-chip/60 px-3 py-2.5 text-xs text-ink-2">
        <input v-model="bindSelf" type="checkbox" class="mt-0.5 accent-blue-600" />
        <span>同时将当前登录账号（<span class="font-medium text-ink">{{ user.email }}</span>）设为管理员，后续免输 Token</span>
      </label>
      <p v-else class="mt-3 text-xs text-muted">
        提示：先在右上角
        <NuxtLink :to="loginRoute('/admin/login')" class="text-brand-ink hover:underline">登录站点账号</NuxtLink>
        再验证 Token，可一键绑定为管理员账号。
      </p>
      <p v-if="failed" class="mt-2 text-sm text-red-600 dark:text-red-400">Token 无效，请重试。</p>
      <button type="submit" class="btn-primary mt-6 w-full px-4 py-2" :disabled="checking">
        {{ checking ? '验证中…' : '登录' }}
      </button>
    </form>
  </div>
</template>
