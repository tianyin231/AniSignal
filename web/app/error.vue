<script setup lang="ts">
import type { NuxtError } from '#app'

const props = defineProps<{ error: NuxtError }>()

const is404 = computed(() => props.error?.statusCode === 404)
const status = computed(() => props.error?.statusCode ?? 500)
const title = computed(() => (is404.value ? '信号丢失' : '页面出了点问题'))
const blurb = computed(() =>
  is404.value
    ? '这个地址可能已下线、拼写有误，或从未存在。资讯还在，只是这条路径没接上。'
    : '服务暂时没能完成这次请求。可以稍后再试，或先回到工作台。',
)

const links = [
  { to: '/', label: '回到工作台' },
  { to: '/intro', label: '产品介绍' },
  { to: '/search', label: '搜索站内内容' },
  { to: '/events', label: '浏览事件' },
  { to: '/legal', label: '条款与隐私' },
]

function goHome() {
  clearError({ redirect: '/' })
}
</script>

<template>
  <div class="relative flex min-h-screen flex-col overflow-hidden bg-slate-50 dark:bg-zinc-950">
    <div class="aurora-bg pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />

    <header class="mx-auto flex w-full max-w-3xl items-center justify-between px-6 pt-6">
      <NuxtLink to="/" class="flex items-center gap-2 text-sm font-medium text-slate-800 dark:text-zinc-100">
        <img src="/icon.png" alt="" class="size-7 rounded-lg" />
        AniSignal
      </NuxtLink>
      <span class="mono text-xs text-slate-400 dark:text-zinc-500">HTTP {{ status }}</span>
    </header>

    <main class="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-6 pb-16 text-center">
      <div
        class="panel w-full max-w-lg rounded-2xl px-6 py-10 sm:px-10 sm:py-12"
      >
        <p
          class="mono text-[11px] tracking-[0.2em] text-blue-600/80 dark:text-blue-300/80"
        >
          {{ is404 ? 'NO SIGNAL' : 'SOMETHING BROKE' }}
        </p>
        <p
          class="mt-4 font-mono text-5xl font-semibold tracking-tight text-slate-900 dark:text-zinc-50 sm:text-6xl"
        >
          {{ status }}
        </p>
        <h1 class="mt-3 text-lg font-semibold text-slate-900 dark:text-zinc-50">
          {{ title }}
        </h1>
        <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
          {{ blurb }}
        </p>

        <div class="mt-6 flex flex-wrap items-center justify-center gap-2">
          <button type="button" class="btn-primary px-4 py-1.5 text-sm" @click="goHome">
            回到首页
          </button>
          <NuxtLink
            to="/search"
            class="rounded-lg border border-slate-200 bg-white/80 px-4 py-1.5 text-sm text-slate-700 backdrop-blur transition hover:border-slate-300 dark:border-white/10 dark:bg-white/5 dark:text-zinc-200 dark:hover:border-white/20"
          >
            去搜索
          </NuxtLink>
        </div>

        <nav class="mt-8 flex flex-wrap justify-center gap-x-4 gap-y-2 text-xs text-slate-500 dark:text-zinc-500">
          <NuxtLink
            v-for="link in links"
            :key="link.to"
            :to="link.to"
            class="transition-colors hover:text-blue-600 dark:hover:text-blue-300"
          >
            {{ link.label }}
          </NuxtLink>
        </nav>
      </div>
    </main>
  </div>
</template>
