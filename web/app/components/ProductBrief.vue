<script setup lang="ts">
import type { ProductBrief } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const props = defineProps<{ brief: ProductBrief }>()

function day(value: string | null) {
  return value ? fmtTime(value).slice(0, 10) : ''
}

const unboxing = computed(() => {
  const brief = props.brief
  if (!brief.unboxing_accounts) return brief.unboxing_note
  const first = day(brief.unboxing_from)
  const last = day(brief.unboxing_to)
  const span = first === last ? first : [first, last].filter(Boolean).join(' – ')
  return [`${brief.unboxing_accounts} 个账号`, span, brief.unboxing_note].filter(Boolean).join(' · ')
})

const quality = computed(() => {
  const brief = props.brief
  if (!brief.quality_accounts) return '暂无质量反馈'
  const latest = brief.quality_latest ? `最新 ${day(brief.quality_latest)}` : ''
  return [`${brief.quality_accounts} 个账号反馈`, latest].filter(Boolean).join(' · ')
})
</script>

<template>
  <div class="grid gap-3">
    <div class="min-w-0">
      <p class="text-xs text-muted">实物开箱</p>
      <p class="mt-0.5 text-[13px] font-semibold leading-snug text-ink-2">{{ unboxing }}</p>
    </div>
    <div class="min-w-0">
      <p class="text-xs text-muted">质量反馈</p>
      <p class="mt-0.5 text-[13px] font-semibold leading-snug text-ink-2">{{ quality }}</p>
    </div>
    <div class="min-w-0">
      <p class="text-xs text-muted">官方说明<span v-if="brief.official_registered"> · 登记信息</span></p>
      <p class="mt-0.5 text-[13px] font-semibold leading-snug text-ink-2">{{ brief.official_text }}</p>
    </div>
  </div>
</template>
