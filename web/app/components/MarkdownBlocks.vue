<script setup lang="ts">
import { parseMarkdownBlocks } from '~/utils/text'

/**
 * 安全的纯文本 Markdown 分块渲染器（零 raw HTML，无 XSS 风险），
 * 统一用于开源项目 README 精读与版本发布说明。
 */
const props = withDefaults(defineProps<{
  source: string
  collapseAfter?: number
  expandLabel?: string
  collapseLabel?: string
}>(), {
  collapseAfter: 0,
  expandLabel: '展开全部内容',
  collapseLabel: '收起内容',
})

const expanded = ref(false)
const blocks = computed(() => parseMarkdownBlocks(props.source))
const canCollapse = computed(() => props.collapseAfter > 0 && blocks.value.length > props.collapseAfter)
const visibleBlocks = computed(() =>
  canCollapse.value && !expanded.value ? blocks.value.slice(0, props.collapseAfter) : blocks.value,
)
</script>

<template>
  <div v-if="blocks.length">
    <div class="space-y-3 text-[14px] leading-relaxed text-ink-2">
      <template v-for="(blk, idx) in visibleBlocks" :key="idx">
        <h3 v-if="blk.type === 'heading' && blk.level === 1" class="pt-2 text-lg font-bold text-ink">{{ blk.text }}</h3>
        <h4 v-else-if="blk.type === 'heading' && blk.level === 2" class="pt-2 text-[15px] font-bold text-ink">{{ blk.text }}</h4>
        <h5 v-else-if="blk.type === 'heading'" class="pt-1 text-sm font-semibold text-ink">{{ blk.text }}</h5>
        <p v-else-if="blk.type === 'paragraph'" class="break-words">{{ blk.text }}</p>
        <ul v-else-if="blk.type === 'list'" class="list-disc space-y-1 pl-5">
          <li v-for="(li, liIdx) in blk.items" :key="liIdx" class="break-words">{{ li }}</li>
        </ul>
        <blockquote v-else-if="blk.type === 'quote'" class="border-l-2 border-brand/50 bg-chip/40 px-3.5 py-2 text-[13px] text-muted">
          {{ blk.text }}
        </blockquote>
        <pre v-else-if="blk.type === 'code'" class="mono overflow-x-auto rounded-xl bg-chip px-3.5 py-3 text-xs leading-relaxed text-ink"><code>{{ blk.text }}</code></pre>
        <hr v-else-if="blk.type === 'hr'" class="border-line-soft">
      </template>
    </div>
    <div v-if="canCollapse" class="mt-4 border-t border-line-soft pt-3 text-center">
      <button
        type="button"
        class="inline-flex h-8 items-center rounded-lg bg-chip px-4 text-xs font-medium text-ink-2 transition-colors hover:bg-line hover:text-ink"
        @click="expanded = !expanded"
      >
        {{ expanded ? collapseLabel : `${expandLabel}（其余 ${blocks.length - collapseAfter} 段）` }}
      </button>
    </div>
  </div>
</template>
