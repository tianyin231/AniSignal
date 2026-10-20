<script setup lang="ts">
const { api } = usePublicApi()
const kinds = [{ key: 'daily', label: '日报' }, { key: 'weekly', label: '周报' }, { key: 'monthly', label: '月报' }]
const { data, error, refresh } = await useAsyncData('ai-reports', async () => Promise.all(kinds.map(async kind => ({ ...kind,
  ...await api<{ items: { key: string; title: string; issueNumber: number; count: number }[] }>(`/api/ai-news/engine/reports/${kind.key}`),
}))))
usePageCommunity(() => 'ai')
useSeoMeta({ title: 'AI 日报 · 周报 · 月报 · AniSignal' })
</script>
<template>
  <div class="space-y-6">
    <NuxtLink to="/ai" class="text-sm text-muted">← AI 快讯</NuxtLink>
    <h1 class="text-2xl font-bold text-ink">AI 刊物</h1>
    <p class="text-sm text-muted">按日、周、月整理重要事实与后续进展。</p>
    <p v-if="error" class="panel p-5">刊物暂不可用。<button class="ml-3 text-brand-ink" @click="refresh()">重试</button></p>
    <div class="grid items-start gap-5 md:grid-cols-3"><section v-for="kind in data" :key="kind.key" class="panel p-5"><h2 class="text-lg font-semibold text-ink">{{ kind.label }}</h2><p v-if="!kind.items.length" class="mt-4 text-sm text-muted">首期尚未发布</p><ul class="mt-3 divide-y divide-line"><li v-for="issue in kind.items" :key="issue.key" class="py-3"><NuxtLink :to="`/ai/reports/${kind.key}/${issue.key}`" class="text-sm text-ink hover:text-brand-ink">{{ issue.title || issue.key }}<span class="mt-1 block text-xs text-muted">第 {{ issue.issueNumber }} 期 · {{ issue.count }} 条</span></NuxtLink></li></ul></section></div>
  </div>
</template>
