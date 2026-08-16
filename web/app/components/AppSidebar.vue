<script setup lang="ts">
import { product, topics } from '~/utils/data'

// 顶栏承载内容主栏目；侧栏只放二级页面与个人区，避免语义重复
const tools = [
  { to: '/?tab=report', label: '今日早报' },
  { to: '/sources', label: '来源健康' },
  { to: '/watch', label: '订阅中心' },
]
</script>

<template>
  <nav class="panel-glass space-y-5 p-3 text-sm">
    <div>
      <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">订阅与工具</p>
      <ul class="space-y-0.5">
        <li v-for="q in tools" :key="q.label">
          <NuxtLink
            :to="q.to"
            class="block truncate rounded-md px-2 py-1.5 text-slate-600 transition-colors hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
          >
            {{ q.label }}
          </NuxtLink>
        </li>
      </ul>
    </div>
    <div>
      <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">监控位</p>
      <div class="px-2 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        <p>1 个运行中 · 爱弥斯键盘</p>
        <p class="mt-0.5 flex items-center gap-1.5">
          <span class="size-1.5 rounded-full bg-emerald-500" />
          即时通知 + 早报
        </p>
      </div>
    </div>
    <div>
      <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">商品</p>
      <NuxtLink
        :to="`/products/${product.slug}`"
        class="block rounded-md px-2 py-1.5 transition-colors hover:bg-slate-200/70 dark:hover:bg-zinc-800/70"
      >
        <p class="truncate text-slate-700 dark:text-zinc-300">{{ product.name }}</p>
        <p class="mt-0.5 text-xs text-slate-400">{{ product.stages[product.currentStage].label }}</p>
      </NuxtLink>
    </div>
    <div>
      <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">热门主题</p>
      <ul class="space-y-0.5">
        <li v-for="t in topics" :key="t.name">
          <NuxtLink
            :to="`/watch?type=topic&topic=${encodeURIComponent(t.name)}`"
            class="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-slate-200/70 dark:hover:bg-zinc-800/70"
          >
            <span class="truncate text-slate-600 dark:text-zinc-300">{{ t.name }}</span>
            <span class="mono shrink-0 text-[11px] text-slate-400 dark:text-zinc-500">{{ t.metric }}</span>
          </NuxtLink>
        </li>
      </ul>
      <p class="mt-1 px-2 text-[11px] text-slate-300 dark:text-zinc-600">主题来自系统预设词典</p>
    </div>
    <p class="px-2 text-xs text-slate-300 dark:text-zinc-600">演示数据 · 非实时</p>
  </nav>
</template>
