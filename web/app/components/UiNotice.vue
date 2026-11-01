<script setup lang="ts">
import { CircleAlert, CircleCheck, Info, TriangleAlert } from 'lucide-vue-next'

// 表单与卡片内的提示条：错误 / 成功 / 注意 / 说明，统一用信号台语义色。
const props = withDefaults(defineProps<{ tone?: 'error' | 'ok' | 'warn' | 'info' }>(), { tone: 'info' })
const styles = {
  error: { box: 'bg-hot/10 text-hot', icon: CircleAlert },
  ok: { box: 'bg-ok/10 text-ok', icon: CircleCheck },
  warn: { box: 'bg-warn/10 text-warn', icon: TriangleAlert },
  info: { box: 'bg-chip text-ink-2', icon: Info },
}
const style = computed(() => styles[props.tone])
</script>

<template>
  <div
    class="flex items-start gap-2 rounded-[10px] px-3 py-2.5 text-[13px] leading-relaxed"
    :class="style.box"
    :role="tone === 'error' || tone === 'warn' ? 'alert' : 'status'"
  >
    <component :is="style.icon" :size="16" class="mt-0.5 shrink-0" aria-hidden="true" />
    <div class="min-w-0 flex-1">
      <slot />
    </div>
  </div>
</template>
