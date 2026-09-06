<script setup lang="ts">
/**
 * 栏目页顶部的返回按钮：回到所属频道的首页。
 * - 分区栏目（/contents?community=blhx）→ 该分区首页 /?community=blhx
 * - 聚合视角（/contents?community=all 或无参数且当前为聚合进入）→ 全站首页 /
 * 放在内容流顶部、随页面滚动，不占用吸顶层。
 */
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

const route = useRoute()
const router = useRouter()
const { community } = useGameSection()

const isAll = computed(() => String(route.query.community || '') === 'all')
const partitionLabel = computed(() => {
  const key = String(route.query.community || '') || community.value
  return GAME_COMMUNITIES.find(c => c.key === key)?.label ?? ''
})
const label = computed(() =>
  isAll.value || !partitionLabel.value ? '返回首页' : `返回${partitionLabel.value}首页`,
)
const target = computed(() => {
  if (isAll.value || !partitionLabel.value) return { path: '/' }
  return { path: '/', query: { community: String(route.query.community) } }
})
</script>

<template>
  <div class="mb-3">
    <button
      type="button"
      class="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-500 transition-colors hover:bg-slate-900/5 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-zinc-100"
      @click="router.push(target)"
    >
      <span aria-hidden="true">←</span>
      {{ label }}
    </button>
  </div>
</template>
