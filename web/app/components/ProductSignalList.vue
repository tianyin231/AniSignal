<script setup lang="ts">
import type { ApiProductSignal } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import { decodeEntities } from '~/utils/text'

const props = defineProps<{ items: ApiProductSignal[]; community?: string }>()

const signalLabels: Record<string, string> = { consumer_received: '实物开箱', quality_issue: '质量反馈' }

interface SignalGroup {
  key: string
  id: number
  labels: string[]
  meta: string
  summary: string
  quote: string
  contentId: number | null
  community?: string | null
  url?: string | null
}

// 同一条内容可能同时是开箱与质量反馈：按内容合并成一个条目、轴徽章并排，
// 不再同标题同链接出现两张卡。摘要里的 HTML 实体一并还原。
const groups = computed<SignalGroup[]>(() => {
  const buckets = new Map<string, ApiProductSignal[]>()
  for (const item of props.items) {
    const key = item.content_id ? `c${item.content_id}` : `s${item.id}`
    const bucket = buckets.get(key)
    if (bucket) bucket.push(item)
    else buckets.set(key, [item])
  }
  return [...buckets.values()].map((bucket) => {
    const first = bucket[0]
    const summary = [...new Set(bucket.map(item => decodeEntities(item.summary || '').trim()).filter(Boolean))].join('；')
    const quote = decodeEntities(
      bucket.map(item => item.claim_quote || '').find(Boolean) || '',
    )
    return {
      key: first.content_id ? `c${first.content_id}` : `s${first.id}`,
      id: first.id,
      labels: [...new Set(bucket.map(item => signalLabels[item.signal_type] || item.signal_label || '动态'))],
      meta: [first.account || first.platform, fmtTime(first.occurred_at)].filter(Boolean).join(' · '),
      summary,
      quote: quote && quote !== summary ? quote : '',
      contentId: first.content_id ?? null,
      community: first.community,
      url: bucket.map(item => item.url).find(Boolean) ?? null,
    }
  })
})
</script>

<template>
  <div v-if="items.length" class="divide-y divide-slate-900/5 dark:divide-white/10">
    <article v-for="group in groups" :key="group.key" class="min-w-0 py-4 first:pt-0 last:pb-0 [overflow-wrap:anywhere]">
      <div class="flex flex-wrap items-center gap-2">
        <span v-for="label in group.labels" :key="label" class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ label }}</span>
        <span class="text-xs text-slate-600 dark:text-zinc-400">{{ group.meta }}</span>
      </div>
      <p class="mt-2 text-sm font-medium text-slate-800 dark:text-zinc-200">{{ group.summary }}</p>
      <blockquote v-if="group.quote" class="mt-2 border-l-2 border-slate-200 pl-3 text-xs leading-relaxed text-slate-500 dark:border-zinc-700 dark:text-zinc-400">
        {{ group.quote }}
      </blockquote>
      <div class="mt-3 flex flex-wrap gap-3 text-xs">
        <NuxtLink v-if="group.contentId" :to="{ path: `/contents/${group.contentId}`, query: { community: group.community || community } }" class="link min-h-11 inline-flex items-center">查看站内原文</NuxtLink>
        <a v-if="group.url" :href="group.url" target="_blank" rel="noopener noreferrer" class="link min-h-11 inline-flex items-center">打开来源 ↗</a>
      </div>
    </article>
  </div>
  <p v-else class="py-8 text-center text-sm text-slate-400">暂无履约信号。</p>
</template>
