<script setup lang="ts">
interface Citation { itemId: string | null; title: string; summary: string | null; sourceName: string; sourceUrl: string; available: boolean }
interface Report { kind: string; key: string; title: string; issueNumber: number; generatedAt: string; overview: string | null; lead: { title: string; leadParagraph: string } | null; highlights: Citation[]; sections: { label: string; summary: string | null; items: Citation[] }[]; flashes: Citation[]; prev: string | null; next: string | null }
const route = useRoute()
const { api } = usePublicApi()
const { data: issue, error, refresh } = await useAsyncData(() => `ai-report-${route.params.kind}-${route.params.key}`,
  (_app, { signal }) => api<Report>(`/api/ai-news/engine/reports/${encodeURIComponent(String(route.params.kind))}/${encodeURIComponent(String(route.params.key))}`, { signal }))
const sections = computed(() => issue.value ? [{ label: '本期重点', summary: null, items: issue.value.highlights }, ...issue.value.sections, { label: '简讯', summary: null, items: issue.value.flashes }].filter(s => s.items.length || s.summary) : [])
usePageCommunity(() => 'ai')
useSeoMeta({ title: () => `${issue.value?.title || 'AI 刊物'} · AniSignal` })
</script>
<template>
  <div class="mx-auto max-w-3xl space-y-5">
    <NuxtLink to="/ai/reports" class="text-sm text-muted">← 全部刊物</NuxtLink>
    <div v-if="error" class="panel p-6">本期尚未发布或暂不可用。<button class="ml-3 text-brand-ink" @click="refresh()">重试</button></div>
    <article v-else-if="issue" class="panel p-5 sm:p-8">
      <p class="text-xs text-muted">{{ issue.key }} · 第 {{ issue.issueNumber }} 期</p>
      <h1 class="mt-3 text-2xl font-bold leading-snug text-ink sm:text-3xl">{{ issue.title }}</h1>
      <p v-if="issue.lead" class="mt-5 whitespace-pre-line text-base leading-8 text-ink-2">{{ issue.lead.leadParagraph }}</p>
      <p v-if="issue.overview" class="mt-4 whitespace-pre-line text-base leading-8 text-ink-2">{{ issue.overview }}</p>
      <section v-for="section in sections" :key="section.label" class="mt-8"><h2 class="border-b border-line pb-3 text-lg font-bold text-ink">{{ section.label }}</h2><p v-if="section.summary" class="mt-3 text-sm leading-7 text-ink-2">{{ section.summary }}</p><ul class="divide-y divide-line"><li v-for="(item, index) in section.items" :key="`${item.itemId}-${index}`" class="py-4"><h3 class="font-semibold text-ink"><NuxtLink v-if="item.available && item.itemId" :to="`/ai/items/${item.itemId}`" class="hover:text-brand-ink">{{ item.title }}</NuxtLink><span v-else class="text-muted">{{ item.title }} · 已撤下</span></h3><p v-if="item.available" class="mt-2 whitespace-pre-line text-sm leading-7 text-ink-2">{{ item.summary }}</p><a v-if="item.available && item.sourceUrl" :href="item.sourceUrl" target="_blank" rel="noopener noreferrer" class="mt-2 inline-block text-xs text-brand-ink">{{ item.sourceName }} ↗</a></li></ul></section>
      <nav class="mt-6 flex justify-between border-t border-line pt-4 text-sm text-brand-ink" aria-label="刊期导航"><NuxtLink v-if="issue.prev" :to="`/ai/reports/${issue.kind}/${issue.prev}`">← 上一期</NuxtLink><NuxtLink v-if="issue.next" :to="`/ai/reports/${issue.kind}/${issue.next}`">下一期 →</NuxtLink></nav>
    </article>
  </div>
</template>
