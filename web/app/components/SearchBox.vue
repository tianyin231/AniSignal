<script setup lang="ts">
import { Search } from 'lucide-vue-next'

const q = ref('')
const router = useRouter()

function go() {
  const s = q.value.trim()
  if (s) router.push({ path: '/search', query: { q: s } })
}
</script>

<template>
  <form class="relative hidden md:block" @submit.prevent="go">
    <Search :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
    <input
      v-model="q"
      type="search"
      aria-label="站内搜索"
      placeholder="搜索事件 / 主题"
      class="w-40 rounded-md border border-slate-200/80 bg-white/60 py-1.5 pl-8 pr-2 text-xs outline-none backdrop-blur transition-all placeholder:text-slate-400 focus:w-56 focus:border-blue-400 dark:border-zinc-700/60 dark:bg-white/5 dark:text-zinc-200"
      @keydown.enter.prevent="go"
    />
  </form>
  <NuxtLink
    to="/search"
    aria-label="搜索"
    class="grid size-8 place-items-center rounded-md text-slate-500 transition-colors hover:bg-slate-200/70 hover:text-slate-900 md:hidden dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
  >
    <Search :size="16" />
  </NuxtLink>
</template>
