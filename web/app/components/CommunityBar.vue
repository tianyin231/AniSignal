<script setup lang="ts">
/**
 * 顶栏下方的分区切换细条：全站常驻（默认布局），底部 3px 强调条随分区变色。
 * 顶栏只保留站点导航，分区维度集中在这条上展示与切换。
 */
import { communityClasses } from '~/composables/useGameSection'

const { community, currentCommunity, setCommunity, communities } = useGameSection()
const accent = computed(() => communityClasses(currentCommunity.value.color))
</script>

<template>
  <div class="glass sticky top-14 z-30">
    <div class="mx-auto flex h-10 max-w-[1280px] items-center gap-3 px-4 lg:px-6">
      <span class="hidden shrink-0 text-xs text-slate-400 sm:block dark:text-zinc-500">游戏分区</span>
      <div
        class="flex items-center rounded-[10px] bg-slate-200/60 p-0.5 dark:bg-zinc-800/70"
        role="tablist"
        aria-label="游戏分区"
      >
        <button
          v-for="c in communities"
          :key="c.key"
          type="button"
          role="tab"
          :aria-selected="community === c.key"
          class="flex cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1 text-[13px] font-medium transition-all"
          :class="
            community === c.key
              ? communityClasses(c.color).tabActive
              : 'text-slate-500 hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-100'
          "
          @click="setCommunity(c.key)"
        >
          <span class="size-1.5 rounded-full" :class="communityClasses(c.color).dot" aria-hidden="true" />
          {{ c.label }}
        </button>
      </div>
      <span class="hidden min-w-0 flex-1 truncate text-xs text-slate-400 md:block dark:text-zinc-500">
        情报、事件与履约信号按分区独立组织
      </span>
    </div>
    <!-- 分区强调条：本条底部 3px 渐变，颜色跟随当前分区 -->
    <div class="pointer-events-none absolute inset-x-0 bottom-0 h-[3px] bg-gradient-to-r" :class="accent.bar" aria-hidden="true" />
  </div>
</template>
