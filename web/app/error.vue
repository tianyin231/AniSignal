<script setup lang="ts">
import type { NuxtError } from '#app'

defineProps<{ error: NuxtError }>()
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center gap-3 bg-slate-100 px-4 text-center dark:bg-zinc-950">
    <p class="mono text-sm text-slate-400">{{ error.statusCode }}</p>
    <h1 class="text-lg font-medium text-slate-900 dark:text-zinc-100">
      {{ error.statusCode === 404 ? '页面不存在或已下线' : '页面出错了' }}
    </h1>
    <button class="btn-primary mt-2 px-4 py-1.5" @click="clearError({ redirect: '/' })">
      回到首页
    </button>
  </div>
</template>
