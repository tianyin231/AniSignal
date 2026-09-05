<script setup lang="ts">
/**
 * 顶栏下方的站点栏目细条：官方动态 / 商品 / 事件 / 内容库（glass 吸顶），
 * 链接自动携带当前分区；底部 3px 分区色强调条随分区变色，是全站的分区标识。
 * 顶栏品牌旁是"首页 + 各分区"频道组。不放解释性文案——吸顶区只留可操作的导航。
 */
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'

const route = useRoute()
const { community, communityQuery } = useGameSection()
const accent = computed(() => {
  const meta = GAME_COMMUNITIES.find(c => c.key === community.value)
  return communityClasses(meta?.color ?? 'blue')
})

const links = [
  { to: '/official', label: '官方动态' },
  { to: '/products', label: '商品' },
  { to: '/events', label: '事件' },
  { to: '/contents', label: '内容库' },
]
const isActive = (to: string) => route.path.startsWith(to)
</script>

<template>
  <div class="glass sticky top-14 z-30">
    <div class="mx-auto flex h-10 max-w-[1280px] items-center gap-4 px-4 lg:px-6">
      <nav class="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto text-sm [scrollbar-width:none]">
        <NuxtLink
          v-for="l in links"
          :key="l.to"
          :to="{ path: l.to, query: communityQuery }"
          class="whitespace-nowrap rounded-md px-2.5 py-1 transition-colors"
          :class="
            isActive(l.to)
              ? 'bg-blue-500/10 font-medium text-blue-600 dark:bg-blue-400/15 dark:text-blue-300'
              : 'text-slate-600 hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100'
          "
        >
          {{ l.label }}
        </NuxtLink>
      </nav>
    </div>
    <!-- 分区强调条：本条底部 3px 渐变，颜色跟随当前分区 -->
    <div class="pointer-events-none absolute inset-x-0 bottom-0 h-[3px] bg-gradient-to-r" :class="accent.bar" aria-hidden="true" />
  </div>
</template>
