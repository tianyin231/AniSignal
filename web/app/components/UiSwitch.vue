<script setup lang="ts">
// 开关：状态由父组件决定（保存成功后再翻转），点击只发出 toggle。
defineProps<{ on: boolean, label: string, disabled?: boolean, busy?: boolean }>()
defineEmits<{ toggle: [] }>()
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="on"
    :aria-label="label"
    :aria-busy="busy || undefined"
    :disabled="disabled || busy"
    class="relative h-7 w-12 shrink-0 rounded-full transition-colors duration-200"
    :class="[on ? 'bg-brand' : 'bg-line', busy ? 'cursor-wait' : '']"
    @click="$emit('toggle')"
  >
    <span
      class="absolute left-1 top-1 size-5 rounded-full bg-white shadow-sm transition-transform duration-200"
      :class="on ? 'translate-x-5' : 'translate-x-0'"
    />
  </button>
</template>
