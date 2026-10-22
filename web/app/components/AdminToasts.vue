<script setup lang="ts">
// 管理端 toast 通知（右下角，带时间戳）
const { toasts } = useAdminToast()

const tone = (type: string) =>
  type === 'ok'
    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
    : type === 'err'
      ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300'
      : 'border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300'
</script>

<template>
  <div class="pointer-events-none fixed bottom-20 right-4 z-50 flex w-80 flex-col gap-2 lg:bottom-4">
    <TransitionGroup name="toast">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="popover pointer-events-auto flex items-start gap-2 border-l-2 p-3 text-sm"
        :class="tone(t.type)"
      >
        <p class="min-w-0 flex-1 leading-snug">{{ t.text }}</p>
        <span class="mono shrink-0 pt-0.5 text-[11px] opacity-60">{{ t.at }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.25s ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(12px);
}
.toast-leave-to {
  opacity: 0;
}
</style>
