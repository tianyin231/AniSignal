<script setup lang="ts">
import { Check, Pipette } from 'lucide-vue-next'
import { PALETTE_SWATCHES, colorToHex, parseHex6 } from '~/utils/community-palette'

const model = defineModel<string>({ default: 'blue' })

const currentHex = computed(() => colorToHex(model.value))
const activeSwatch = computed(() =>
  PALETTE_SWATCHES.find(s => s.value === model.value || s.hex.toLowerCase() === currentHex.value.toLowerCase()),
)
const currentLabel = computed(() => activeSwatch.value?.label ?? currentHex.value.toUpperCase())

function isSelected(swatchValue: string, swatchHex: string): boolean {
  if (model.value === swatchValue) return true
  const hex6 = parseHex6(model.value)
  return Boolean(hex6 && `#${hex6}` === swatchHex.toLowerCase())
}

function onCustomColor(event: Event) {
  const val = (event.target as HTMLInputElement).value
  if (parseHex6(val)) model.value = val.toLowerCase()
}
</script>

<template>
  <div class="rounded-xl border border-line bg-canvas/50 p-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2 text-xs">
        <span class="font-medium text-ink-2">标签调色板</span>
        <span
          class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-medium"
          :style="{ backgroundColor: `${currentHex}22`, color: currentHex }"
        >
          <span class="size-2 rounded-full" :style="{ backgroundColor: currentHex }" />
          {{ currentLabel }}
        </span>
        <span class="mono text-[11px] text-faint">{{ currentHex.toUpperCase() }}</span>
      </div>

      <label
        class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-card px-2.5 py-1 text-xs text-ink-2 transition-colors hover:bg-chip hover:text-ink"
        title="打开系统取色器自由选择任意颜色"
      >
        <Pipette :size="13" />
        <span>自由取色</span>
        <input
          type="color"
          :value="currentHex"
          aria-label="自定义十六进制主题色"
          class="sr-only"
          @input="onCustomColor"
        >
      </label>
    </div>

    <div class="mt-2.5 flex flex-wrap gap-1.5" role="radiogroup" aria-label="预设主题色调色板">
      <button
        v-for="swatch in PALETTE_SWATCHES"
        :key="swatch.value"
        type="button"
        role="radio"
        :aria-checked="isSelected(swatch.value, swatch.hex)"
        :aria-label="`${swatch.label} (${swatch.hex})`"
        :title="`${swatch.label} · ${swatch.hex.toUpperCase()}`"
        class="grid size-6 place-items-center rounded-full border border-black/10 transition-transform hover:scale-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand dark:border-white/15"
        :class="isSelected(swatch.value, swatch.hex) ? 'ring-2 ring-ink ring-offset-2 ring-offset-card scale-105' : ''"
        :style="{ backgroundColor: swatch.hex }"
        @click="model = swatch.value"
      >
        <Check v-if="isSelected(swatch.value, swatch.hex)" :size="13" class="text-white drop-shadow" />
      </button>
    </div>
  </div>
</template>
