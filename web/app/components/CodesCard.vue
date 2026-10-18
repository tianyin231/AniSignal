<script setup lang="ts">
import { Check, Copy } from 'lucide-vue-next'
import type { RedeemCodeDto } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { fmtTime } from '~/utils/admin-types'

// 兑换码：官方内容抽取，点击复制
const props = defineProps<{ codes: RedeemCodeDto[], showCommunity?: boolean }>()
const copied = ref('')

async function copy(code: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(code)
    } else {
      // HTTP 部署下没有异步剪贴板 API，回退到隐藏文本域 + execCommand
      const box = document.createElement('textarea')
      box.value = code
      box.style.position = 'fixed'
      box.style.opacity = '0'
      document.body.appendChild(box)
      box.select()
      document.execCommand('copy')
      box.remove()
    }
    copied.value = code
    setTimeout(() => { if (copied.value === code) copied.value = '' }, 1500)
  } catch {}
}
const rows = computed(() => props.codes.map((c) => {
  const meta = props.showCommunity ? communityMeta(c.community) : undefined
  return { ...c, game: meta?.label, chip: meta ? communityClasses(meta.color).chip : '' }
}))
</script>

<template>
  <section v-if="rows.length" id="codes" class="panel px-5 pb-2 pt-4" aria-labelledby="codes-title">
    <h2 id="codes-title" class="text-base font-bold text-ink">兑换码</h2>
    <ul class="mt-2">
      <li v-for="c in rows" :key="`${c.community}-${c.code}`" class="flex items-center gap-3 border-t border-line-soft py-2.5">
        <button
          type="button"
          class="mono inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand-soft px-3 text-sm font-semibold text-brand-ink transition-colors hover:bg-brand/20"
          :aria-label="copied === c.code ? '已复制' : `复制兑换码 ${c.code}`"
          @click="copy(c.code)"
        >
          {{ c.code }}
          <Check v-if="copied === c.code" :size="14" aria-hidden="true" />
          <Copy v-else :size="13" class="opacity-60" aria-hidden="true" />
        </button>
        <span class="min-w-0 flex-1 truncate text-xs text-muted">
          <span v-if="c.game" class="mr-1 rounded px-1 py-0.5 font-semibold" :class="c.chip">{{ c.game }}</span>
          {{ c.expires_at ? `${fmtTime(c.expires_at)} 前有效` : '有效期待公布' }}
        </span>
        <a v-if="c.url" :href="c.url" target="_blank" rel="noopener" class="link shrink-0 text-xs">来源</a>
      </li>
    </ul>
  </section>
</template>
