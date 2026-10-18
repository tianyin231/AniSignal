<script setup lang="ts">
interface Tab {
  key: string
  label: string
}
defineProps<{ tabs: Tab[]; modelValue: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()
</script>

<template>
  <div class="sticky top-[100px] z-30 mb-4 md:top-16">
    <div class="panel flex max-w-full items-center justify-between gap-2 rounded-[14px] p-1">
      <div class="inline-flex gap-0.5 overflow-x-auto [scrollbar-width:none]" role="tablist">
        <!-- 选中态用频道强调色：进入游戏频道后随该游戏品牌色变化 -->
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          role="tab"
          :aria-selected="modelValue === t.key"
          class="h-9 whitespace-nowrap rounded-[10px] px-3.5 text-sm transition-colors"
          :class="modelValue === t.key ? 'bg-accent-soft font-semibold text-accent-ink' : 'text-muted hover:bg-chip hover:text-ink'"
          @click="emit('update:modelValue', t.key)"
        >
          {{ t.label }}
        </button>
      </div>
      <!-- 右侧动作区（可选）：随吸顶行常驻 -->
      <slot name="actions" />
    </div>
  </div>
</template>
