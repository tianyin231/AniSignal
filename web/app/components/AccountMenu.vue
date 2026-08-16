<script setup lang="ts">
import { BellRing, ChevronDown, LogOut, Mail, Settings, User } from 'lucide-vue-next'

const open = ref(false)
const route = useRoute()
watch(
  () => route.fullPath,
  () => {
    open.value = false
  },
)
</script>

<template>
  <div class="relative">
    <button
      type="button"
      aria-label="账号菜单"
      class="flex items-center gap-1.5 rounded-full border py-1 pl-1 pr-2 transition-colors"
      :class="
        open
          ? 'border-blue-400/70 bg-blue-500/5 dark:border-blue-400/40'
          : 'border-slate-200/80 hover:border-slate-300 dark:border-zinc-700/80 dark:hover:border-zinc-600'
      "
      @click="open = !open"
    >
      <span class="grid size-6 place-items-center rounded-full bg-blue-500/15 text-blue-600 dark:text-blue-300">
        <User :size="13" />
      </span>
      <span class="hidden text-xs text-slate-600 sm:block dark:text-zinc-300">演示用户</span>
      <ChevronDown :size="12" class="text-slate-400 transition-transform" :class="open ? 'rotate-180' : ''" />
    </button>

    <div v-if="open" class="fixed inset-0 z-40" @click="open = false" />
    <div
      v-if="open"
      class="popover absolute right-0 top-[calc(100%+8px)] z-50 w-60 p-1.5"
    >
      <div class="px-2.5 py-2">
        <p class="text-sm font-medium text-slate-800 dark:text-zinc-100">演示用户</p>
        <p class="mono mt-0.5 flex items-center gap-1 text-xs text-slate-400 dark:text-zinc-500">
          <Mail :size="11" />demo@anisignal.dev
        </p>
        <p class="mt-1.5">
          <span class="chip bg-blue-500/10 font-medium text-blue-600 dark:text-blue-300">基础订阅 · 剩余 23 天</span>
        </p>
      </div>
      <div class="my-1 border-t border-slate-200/70 dark:border-zinc-700/60" />
      <NuxtLink
        to="/watch"
        class="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800/60"
      >
        <BellRing :size="14" class="text-slate-400" />订阅中心与发送记录
      </NuxtLink>
      <a
        href="#"
        class="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800/60"
        @click.prevent
      >
        <Settings :size="14" class="text-slate-400" />账号设置
      </a>
      <div class="my-1 border-t border-slate-200/70 dark:border-zinc-700/60" />
      <button
        type="button"
        class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800/60"
        @click.prevent
      >
        <LogOut :size="14" class="text-slate-400" />退出登录
      </button>
    </div>
  </div>
</template>
