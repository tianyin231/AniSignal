<script setup lang="ts">
// 顶栏承载内容主栏目；侧栏只放二级页面（商品入口在顶栏与右栏）
const tools = [
  { to: '/sources', label: '来源健康' },
  { to: '/watch', label: '订阅中心' },
]

</script>

<template>
  <nav class="panel-glass space-y-5 p-3 text-sm">
    <div>
      <p class="px-2 pb-1.5 text-xs font-medium text-slate-400 dark:text-zinc-500">工具</p>
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
  </nav>
</template>
