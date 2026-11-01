<script setup lang="ts">
import { Eye, EyeOff } from 'lucide-vue-next'

// 密码输入：可切换明文，开着大写锁定时提示（最常见的"密码明明对却登不上"）。
withDefaults(defineProps<{
  id: string
  label: string
  autocomplete: 'current-password' | 'new-password'
  placeholder?: string
  hint?: string
  invalid?: boolean
}>(), { placeholder: '', hint: '', invalid: false })
const model = defineModel<string>({ default: '' })
const visible = ref(false)
const capsLock = ref(false)
function detectCaps(event: KeyboardEvent) {
  capsLock.value = event.getModifierState?.('CapsLock') ?? false
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between gap-2">
      <label :for="id" class="text-sm font-medium text-ink-2">{{ label }}</label>
      <slot name="aside" />
    </div>
    <div class="relative mt-1.5">
      <input
        :id="id"
        v-model="model"
        :type="visible ? 'text' : 'password'"
        :autocomplete="autocomplete"
        :placeholder="placeholder"
        :aria-invalid="invalid || undefined"
        :aria-describedby="`${id}-hint`"
        autocapitalize="off"
        spellcheck="false"
        class="input h-11 w-full pr-11"
        :class="invalid ? 'border-hot!' : ''"
        @keydown="detectCaps"
        @keyup="detectCaps"
        @blur="capsLock = false"
      >
      <button
        type="button"
        class="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-[10px] text-faint transition-colors hover:text-ink-2"
        :aria-label="visible ? '隐藏密码' : '显示密码'"
        :aria-pressed="visible"
        @click="visible = !visible"
      >
        <EyeOff v-if="visible" :size="17" />
        <Eye v-else :size="17" />
      </button>
    </div>
    <p :id="`${id}-hint`" class="mt-1.5 min-h-4 text-xs" :class="capsLock ? 'text-warn' : invalid ? 'text-hot' : 'text-faint'">
      {{ capsLock ? '大写锁定已开启' : hint }}
    </p>
  </div>
</template>
