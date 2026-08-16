<script setup lang="ts">
interface Tab {
  key: string
  label: string
}
defineProps<{ tabs: Tab[]; modelValue: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()
</script>

<template>
  <div class="sticky top-16 z-30 mb-4">
    <div class="panel-glass inline-flex max-w-full gap-0.5 overflow-x-auto rounded-[14px] p-1 [scrollbar-width:none]">
      <button
        v-for="t in tabs"
        :key="t.key"
        type="button"
        class="whitespace-nowrap rounded-[10px] px-3 py-1.5 text-sm transition-all"
        :class="
          modelValue === t.key
            ? 'bg-white/85 font-medium text-blue-700 shadow-sm dark:bg-white/10 dark:text-blue-300'
            : 'text-slate-500 hover:text-slate-800 dark:text-zinc-400 dark:hover:text-zinc-200'
        "
        @click="emit('update:modelValue', t.key)"
      >
        {{ t.label }}
      </button>
    </div>
  </div>
</template>
