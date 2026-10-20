<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
interface Report { id: string; title: string; summary: string | null; source: { name: string }; publishedAt: string }
interface Story {
  title: string; digest: string | null; summary: string | null; latest: string | null
  sourceCount: number; reportCount: number; latestAt: string | null; status: string
  developments: { factId: string; title: string; firstReportAt: string; reportCount: number; representative: Report }[]
  timeline: Report[]; related: { publicId: string; title: string }[]
}
const route = useRoute()
const { api } = usePublicApi()
const { data: story, error, refresh } = await useAsyncData(() => `ai-story-${route.params.id}`,
  (_app, { signal }) => api<Story>(`/api/ai-news/engine/stories/${encodeURIComponent(String(route.params.id))}`, { signal }))
usePageCommunity(() => 'ai')
useSeoMeta({ title: () => `${story.value?.title || '事件进展'} · AniSignal` })
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-5">
    <NuxtLink to="/ai?mode=hot" class="text-sm text-muted hover:text-ink">← AI 热点</NuxtLink>
    <div v-if="error" class="panel p-6">事件暂不可用。<button class="ml-3 text-brand-ink" @click="refresh()">重试</button></div>
    <template v-else-if="story">
      <section class="panel p-5 sm:p-8">
        <p class="mb-3 text-xs text-muted">{{ story.sourceCount }} 个来源 · {{ story.reportCount }} 篇报道 · {{ story.latestAt ? fmtTime(story.latestAt) : '' }}</p>
        <h1 class="text-2xl font-bold leading-snug text-ink sm:text-3xl">{{ story.title }}</h1>
        <p v-if="story.digest || story.summary" class="mt-5 whitespace-pre-line text-base leading-8 text-ink-2">{{ story.digest || story.summary }}</p>
        <p v-if="story.latest" class="mt-4 rounded-xl bg-chip p-4 text-sm leading-7 text-ink-2">最新进展：{{ story.latest }}</p>
      </section>
      <section class="panel p-5 sm:p-8">
        <h2 class="text-lg font-bold text-ink">事实与进展</h2>
        <ol class="mt-5 space-y-6 border-l border-line pl-5"><li v-for="fact in story.developments" :key="fact.factId">
          <time class="text-xs text-muted">{{ fmtTime(fact.firstReportAt) }} · {{ fact.reportCount }} 篇报道</time>
          <h3 class="mt-1 font-semibold text-ink">{{ fact.title }}</h3>
          <p class="mt-2 text-sm leading-7 text-ink-2">{{ fact.representative.summary }}</p>
          <NuxtLink :to="`/ai/items/${fact.representative.id}`" class="mt-2 inline-block text-sm text-brand-ink">{{ fact.representative.source.name }} · 阅读报道 →</NuxtLink>
        </li></ol>
      </section>
      <section class="panel p-5 sm:p-8"><h2 class="text-lg font-bold text-ink">报道时间线</h2><ul class="mt-4 divide-y divide-line"><li v-for="report in story.timeline" :key="report.id" class="py-3"><NuxtLink :to="`/ai/items/${report.id}`" class="text-sm font-semibold text-ink hover:text-brand-ink">{{ report.title }}</NuxtLink><p class="mt-1 text-xs text-muted">{{ report.source.name }} · {{ fmtTime(report.publishedAt) }}</p></li></ul></section>
      <section v-if="story.related.length" class="panel p-5"><h2 class="font-semibold text-ink">相关事件</h2><NuxtLink v-for="related in story.related" :key="related.publicId" :to="`/ai/stories/${related.publicId}`" class="mt-3 block text-sm text-brand-ink">{{ related.title }} →</NuxtLink></section>
    </template>
  </div>
</template>
