<script setup lang="ts">
import type { ProductBrief } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

const props = defineProps<{ brief: ProductBrief; compact?: boolean; clampOfficial?: boolean }>()

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
  <div class="grid gap-3" :class="{ 'grid-cols-2 gap-x-5': compact }">
    <div class="min-w-0">
      <p class="text-xs text-muted">实物开箱</p>
      <template v-if="compact && brief.unboxing_accounts">
        <p class="mt-1 text-xs text-muted"><span class="num mr-1 text-2xl font-bold text-ink">{{ brief.unboxing_accounts }}</span>个账号</p>
        <p class="mono mt-1 text-[11px] text-muted">{{ day(brief.unboxing_to) }}</p>
      </template>
      <p v-else class="mt-0.5 text-[13px] font-semibold leading-snug text-ink-2">{{ unboxing }}</p>
    </div>
    <div class="min-w-0">
      <p class="text-xs text-muted">质量反馈</p>
      <template v-if="compact && brief.quality_accounts">
        <p class="mt-1 text-xs text-muted"><span class="num mr-1 text-2xl font-bold text-ink">{{ brief.quality_accounts }}</span>个账号反馈</p>
        <p class="mono mt-1 text-[11px] text-muted">{{ day(brief.quality_latest) }}</p>
      </template>
      <p v-else class="mt-0.5 text-[13px] font-semibold leading-snug text-ink-2">{{ quality }}</p>
    </div>
    <div class="min-w-0" :class="{ 'col-span-2 border-t border-line-soft pt-3': compact }">
      <p class="text-xs text-muted">官方说明<span v-if="brief.official_registered"> · 登记信息</span></p>
      <p class="mt-1 text-[13px] font-medium leading-relaxed text-ink-2" :class="{ 'line-clamp-3': clampOfficial }">{{ brief.official_text }}</p>
    </div>
  </div>
</template>
