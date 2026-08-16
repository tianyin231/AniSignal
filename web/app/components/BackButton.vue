<script setup lang="ts">
import { ArrowLeft } from 'lucide-vue-next'

const props = withDefaults(defineProps<{ label?: string; fallback?: string }>(), {
  label: '返回',
  fallback: '/',
})

const router = useRouter()
function goBack() {
  // 站内有历史则回退；直达链接（分享进入）落到 fallback 页
  if (window.history.state?.back) router.back()
  else router.push(props.fallback)
}
</script>

<template>
  <button
    type="button"
    class="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-slate-500 transition-colors hover:bg-slate-200/60 hover:text-slate-800 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-200"
    @click="goBack"
  >
    <ArrowLeft :size="13" />
    {{ label }}
  </button>
</template>
