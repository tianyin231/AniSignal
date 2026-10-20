<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
interface Source { id?: string; name: string; platform: string; status: string; last_success_at: string | null; interval_minutes: number }
const { api } = usePublicApi()
const { data, error, refresh } = await useAsyncData('ai-public-sources',
  (_app, { signal }) => api<{ sources: Source[] }>('/api/ai-news/sources', { signal }))
const rss = computed(() => data.value?.sources.filter(s => s.platform === 'rss') || [])
const platforms = computed(() => data.value?.sources.filter(s => s.platform !== 'rss') || [])
const labels: Record<string, string> = { active: '正常', delayed: '更新延迟', pending: '等待首轮' }
usePageCommunity(() => 'ai')
useSeoMeta({ title: 'AI 新闻来源 · AniSignal' })
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-6">
    <NuxtLink to="/ai" class="text-sm text-muted hover:text-ink">← AI 快讯</NuxtLink>
    <header><h1 class="text-2xl font-bold text-ink">AI 新闻来源</h1><p class="mt-3 text-sm leading-7 text-muted">新闻 RSS 与现有平台采集共同提供资料。每篇报道保留原文链接；来源正常表示资料入口可用。</p></header>
    <p v-if="error" class="panel p-5 text-ink-2">来源暂不可用。<button class="ml-3 text-brand-ink" @click="refresh()">重新加载</button></p>
    <section v-for="group in [{ label: '新闻 RSS', items: rss }, { label: '平台来源', items: platforms }]" :key="group.label" class="space-y-3">
      <h2 class="text-lg font-semibold text-ink">{{ group.label }} <span class="num text-sm text-muted">{{ group.items.length }}</span></h2>
      <ul class="grid gap-3 sm:grid-cols-2"><li v-for="(source, index) in group.items" :key="source.id || index" class="panel p-4"><div class="flex items-start justify-between gap-3"><h3 class="min-w-0 break-words text-sm font-semibold text-ink">{{ source.name }}</h3><span class="shrink-0 text-xs" :class="source.status === 'active' ? 'text-ok' : source.status === 'delayed' ? 'text-warn' : 'text-muted'">{{ labels[source.status] || source.status }}</span></div><p class="mt-2 text-xs text-muted">{{ source.last_success_at ? `最近成功：${fmtTime(source.last_success_at)}` : source.status === 'active' ? '近期已有资料入库' : '等待采集任务更新' }}</p><p v-if="source.platform === 'rss'" class="mt-1 text-xs text-faint">当前采集间隔 {{ source.interval_minutes }} 分钟</p></li></ul>
    </section>
  </div>
</template>
