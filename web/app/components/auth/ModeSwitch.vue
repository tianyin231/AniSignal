<script setup lang="ts">
// 登录 / 注册切换：保留 ?redirect=，并把已填写的邮箱带到另一页。
const props = defineProps<{ active: 'login' | 'register', email?: string }>()
const { withRedirect } = useAuthRedirect()
const flash = useAuthFlash()
const modes = [
  { key: 'login', label: '登录', path: '/login' },
  { key: 'register', label: '注册', path: '/register' },
] as const
function carryEmail() {
  const email = props.email?.trim()
  if (email) flash.value = { email }
}
</script>

<template>
  <nav class="grid grid-cols-2 gap-1 rounded-xl bg-chip p-1 text-sm" aria-label="登录或注册">
    <NuxtLink
      v-for="mode in modes"
      :key="mode.key"
      :to="withRedirect(mode.path)"
      replace
      class="grid h-9 place-items-center rounded-[9px] transition-colors"
      :class="active === mode.key ? 'bg-card font-semibold text-ink shadow-card' : 'text-muted hover:text-ink'"
      :aria-current="active === mode.key ? 'page' : undefined"
      @click="carryEmail"
    >
      {{ mode.label }}
    </NuxtLink>
  </nav>
</template>
