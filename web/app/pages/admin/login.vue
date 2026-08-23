<script setup lang="ts">
definePageMeta({ layout: 'admin' })
useSeoMeta({ title: '登录 · AniSignal 管理看板' })

const { login } = useAdminApi()
const tokenInput = ref('')
const checking = ref(false)
const failed = ref(false)

async function submit() {
  if (!tokenInput.value.trim() || checking.value) return
  checking.value = true
  failed.value = false
  const ok = await login(tokenInput.value.trim())
  checking.value = false
  if (ok) navigateTo('/admin', { replace: true })
  else failed.value = true
}
</script>

<template>
  <div class="flex min-h-[60vh] items-center justify-center">
    <form class="panel w-full max-w-sm p-8" @submit.prevent="submit">
      <h1 class="text-lg font-semibold">管理员登录</h1>
      <p class="mt-1 text-sm text-slate-500 dark:text-zinc-400">
        输入后端配置的管理员 Token（<span class="mono">ANISIGNAL_ADMIN_TOKEN</span>）。
      </p>
      <label class="mt-6 block text-sm font-medium" for="token">Token</label>
      <input
        id="token"
        v-model="tokenInput"
        type="password"
        autocomplete="current-password"
        placeholder="admin token"
        class="mono mt-1.5 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm outline-none focus:border-blue-500 dark:border-white/15 dark:bg-white/5"
      />
      <p v-if="failed" class="mt-2 text-sm text-red-600 dark:text-red-400">Token 无效，请重试。</p>
      <button type="submit" class="btn-primary mt-6 w-full px-4 py-2" :disabled="checking">
        {{ checking ? '验证中…' : '登录' }}
      </button>
    </form>
  </div>
</template>
