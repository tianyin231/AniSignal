<script setup lang="ts">
// 6 位邮箱验证码：只保留数字（粘贴"123 456"也能识别），填满后通知父组件可直接提交。
const props = withDefaults(defineProps<{ label?: string, autofocus?: boolean }>(), { label: '6 位验证码', autofocus: false })
const model = defineModel<string>({ default: '' })
const emit = defineEmits<{ complete: [] }>()
// 验证步骤是切换出来的，原生 autofocus 只在首次加载生效，这里手动聚焦。
const field = ref<HTMLInputElement | null>(null)
onMounted(() => { if (props.autofocus) field.value?.focus() })

function onInput(event: Event) {
  const input = event.target as HTMLInputElement
  const digits = input.value.replace(/\D/g, '').slice(0, 6)
  input.value = digits
  model.value = digits
  if (digits.length === 6) emit('complete')
}
</script>

<template>
  <input
    :value="model"
    type="text"
    inputmode="numeric"
    autocomplete="one-time-code"
    ref="field"
    :aria-label="label"
    placeholder="000000"
    class="input num h-14 w-full text-center text-[26px] font-bold tracking-[0.45em] placeholder:text-line"
    @input="onInput"
  >
</template>
