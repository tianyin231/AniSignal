<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
interface Item {
  id: string; title: string; originalTitle: string | null; summary: string | null; reason: string | null
  source: { name: string }; links: { original: string }; publishedAt: string | null; selected: boolean
  readingMode: string; body: { zh: string | null; original: string | null; complete: boolean } | null
  hasTranslation: boolean; group: { factId: string; additionalSourceCount: number } | null
  story: { publicId: string; title: string } | null; relatedStories: { publicId: string; title: string }[]
}
const route = useRoute()
const { api } = usePublicApi()
const original = ref(false)
const { data: item, error, refresh } = await useAsyncData(
  () => `ai-item-${route.params.id}-${original.value}`,
  (_app, { signal }) => api<Item>(`/api/ai-news/engine/items/${encodeURIComponent(String(route.params.id))}${original.value ? '/original' : ''}`, { signal }),
)
usePageCommunity(() => 'ai')
useSeoMeta({ title: () => `${item.value?.title || '快讯详情'} · AniSignal`, description: () => item.value?.summary || '' })
const { data: reports } = await useAsyncData(() => `ai-group-${item.value?.group?.factId}`, async () => {
  if (!item.value?.group?.factId) return null
  return api<{ reports: { id: string; title: string; source: { name: string }; originalUrl: string }[] }>(`/api/ai-news/engine/groups/${item.value.group.factId}/reports`)
})
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-5">
    <NuxtLink to="/ai" class="text-sm text-muted hover:text-ink">← AI 快讯</NuxtLink>
    <div v-if="error" class="panel p-6"><p class="text-ink">这条报道暂不可用，可能已撤稿。</p><button class="mt-4 text-sm text-brand-ink" @click="refresh()">重新加载</button></div>
    <article v-else-if="item" class="panel p-5 sm:p-8">
      <p class="mb-3 text-xs text-muted">{{ item.selected ? '编辑精选' : 'AI 资讯' }} · {{ item.source.name }} · {{ item.publishedAt ? fmtTime(item.publishedAt) : '来源时间未知' }}</p>
      <h1 class="text-2xl font-bold leading-snug text-ink sm:text-3xl">{{ item.title }}</h1>
      <p v-if="item.originalTitle && item.originalTitle !== item.title" class="mt-3 text-sm text-muted">{{ item.originalTitle }}</p>
      <p v-if="item.summary" class="mt-5 whitespace-pre-line text-base leading-8 text-ink-2">{{ item.summary }}</p>
      <p v-if="item.reason" class="mt-4 border-l-2 border-ai pl-4 text-sm leading-7 text-ink-2">精选理由：{{ item.reason }}</p>
      <div class="my-6 flex flex-wrap gap-4 border-y border-line py-3 text-sm">
        <a :href="item.links.original" target="_blank" rel="noopener noreferrer" class="text-brand-ink">查看来源原文 ↗</a>
        <button v-if="item.hasTranslation" class="text-ink-2" @click="original = !original">{{ original ? '阅读中文' : '阅读原文' }}</button>
        <NuxtLink v-if="item.story" :to="`/ai/stories/${item.story.publicId}`" class="text-brand-ink">事件进展 →</NuxtLink>
      </div>
      <p v-if="item.readingMode === 'summary-only'" class="text-sm text-muted">本站展示摘要，完整内容请阅读来源原文。</p>
      <!-- HTML 由原版 publication/detail 的白名单净化器生成。 -->
      <div v-if="item.body?.zh || item.body?.original" class="ai-article text-[15px] leading-8 text-ink-2" v-html="item.body.zh || item.body.original" />
      <p v-if="item.body && !item.body.complete" class="mt-4 text-xs text-muted">正文尚未完整，详细内容以来源原文为准。</p>
    </article>
    <section v-if="reports?.reports?.length" class="panel p-5">
      <h2 class="font-semibold text-ink">同一事实的其他报道</h2>
      <ul class="mt-3 space-y-3"><li v-for="report in reports.reports" :key="report.id"><NuxtLink :to="`/ai/items/${report.id}`" class="text-sm text-ink-2 hover:text-brand-ink">{{ report.title }} <span class="text-xs text-muted">· {{ report.source.name }}</span></NuxtLink></li></ul>
    </section>
    <p class="text-xs text-muted">AI 整理并保留来源；精选代表编辑判断。</p>
  </div>
</template>

<style scoped>
.ai-article :deep(p), .ai-article :deep(ul), .ai-article :deep(ol) { margin: 1em 0; }
.ai-article :deep(a) { color: var(--sd-brand-ink); text-decoration: underline; }
.ai-article :deep(img), .ai-article :deep(video) { max-width: 100%; border-radius: 12px; }
.ai-article :deep(pre) { overflow-x: auto; padding: 1rem; background: var(--sd-chip); border-radius: 8px; }
.ai-article :deep(h2), .ai-article :deep(h3) { margin: 1.5em 0 .5em; font-weight: 700; color: var(--sd-ink); }
</style>
