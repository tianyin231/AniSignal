<script setup lang="ts">
import { ArrowLeft, Check, Copy, ExternalLink, Sparkles } from 'lucide-vue-next'
import { REPORT_PERIOD_LABELS, platformLabel } from '~/utils/lexicon'
import { fmtTime } from '~/utils/admin-types'

interface ReportItem {
  title: string
  summary: string
  url: string
  detail_url: string
  source: string
  platform: string
  metrics_text: string
  published_at: string | null
  content_id: number | null
}

interface ReportSection {
  label: string
  summary: string
  items: ReportItem[]
}

interface ReportDetail {
  id: number
  scope_type: 'community' | 'custom_bundle'
  scope_key: string
  scope_label: string
  period: 'daily' | 'weekly'
  period_key: string
  title: string
  overview: string
  sections: ReportSection[]
  markdown: string
  item_count: number
  ai_generated: boolean
  model: string | null
  window_start: string
  window_end: string
  created_at: string
  prev_id: number | null
  next_id: number | null
}

const route = useRoute()
const { api } = usePublicApi()
const copied = ref(false)

const { data: report, error, refresh } = await useAsyncData(
  () => `periodic-report-${route.params.id}`,
  (_app, { signal }) => api<ReportDetail>(`/api/reports/${encodeURIComponent(String(route.params.id))}`, { signal }),
)

useSeoMeta({
  title: () => `${report.value?.title || '周期报告'} · AniSignal`,
  description: () => report.value?.overview || 'AniSignal 周期情报报告',
})

async function copyMarkdown() {
  if (!report.value?.markdown) return
  try {
    await navigator.clipboard.writeText(report.value.markdown)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1800)
  } catch { /* ignore clipboard errors */ }
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-5">
    <div class="flex items-center justify-between gap-3">
      <NuxtLink to="/reports" class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft :size="15" aria-hidden="true" /> 返回全部报告
      </NuxtLink>
      <NuxtLink to="/account?tab=notify#periodic-reports" class="text-xs font-medium text-brand-ink hover:underline">
        管理定时推送
      </NuxtLink>
    </div>

    <div v-if="error" class="panel p-6 text-sm text-muted">
      报告不存在或暂不可访问。
      <button type="button" class="ml-2 font-semibold text-brand-ink hover:underline" @click="refresh()">重试</button>
    </div>

    <article v-else-if="report" class="panel p-5 sm:p-8">
      <header class="flex flex-col items-start gap-3 sm:flex-row sm:justify-between sm:gap-4">
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-1.5 text-xs">
            <span class="rounded bg-brand-soft px-2 py-0.5 font-semibold text-brand-ink">
              {{ REPORT_PERIOD_LABELS[report.period] ?? report.period }}
            </span>
            <span class="rounded bg-chip px-2 py-0.5 text-ink-2">{{ report.scope_label }}</span>
            <span v-if="report.ai_generated" class="inline-flex items-center gap-1 rounded bg-chip px-2 py-0.5 text-brand-ink">
              <Sparkles :size="11" aria-hidden="true" /> AI 聚类
            </span>
            <span class="mono tabular-nums text-faint">{{ report.period_key }}</span>
          </div>
          <h1 class="mt-3 text-2xl font-bold leading-snug text-ink sm:text-[28px]">
            {{ report.title }}
          </h1>
          <p class="mono mt-2 text-xs tabular-nums text-faint">
            统计区间 {{ fmtTime(report.window_start) }} – {{ fmtTime(report.window_end) }} · 共 {{ report.item_count }} 条
          </p>
        </div>

        <button
          type="button"
          class="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-[10px] border border-line px-3 text-xs font-medium text-ink-2 transition-colors hover:bg-chip hover:text-ink"
          @click="copyMarkdown"
        >
          <Check v-if="copied" :size="14" class="text-ok" aria-hidden="true" />
          <Copy v-else :size="14" aria-hidden="true" />
          {{ copied ? '已复制 Markdown' : '复制 Markdown' }}
        </button>
      </header>

      <div v-if="report.overview" class="mt-5 rounded-xl bg-chip/55 px-4 py-3.5 text-sm leading-7 text-ink-2">
        {{ report.overview }}
      </div>

      <section
        v-for="section in report.sections"
        :key="section.label"
        class="mt-7"
      >
        <div class="border-b border-line pb-2.5">
          <h2 class="text-lg font-bold text-ink">{{ section.label }}</h2>
          <p v-if="section.summary" class="mt-1 text-xs leading-relaxed text-muted">{{ section.summary }}</p>
        </div>

        <ul class="divide-y divide-line-soft">
          <li v-for="(item, idx) in section.items" :key="`${item.title}-${idx}`" class="py-4">
            <div class="flex items-start justify-between gap-3">
              <h3 class="min-w-0 flex-1 text-[15px] font-semibold leading-snug text-ink">
                <NuxtLink
                  v-if="item.detail_url && item.detail_url.startsWith('/')"
                  :to="item.detail_url"
                  class="hover:text-brand-ink"
                >
                  {{ item.title }}
                </NuxtLink>
                <a
                  v-else-if="item.url"
                  :href="item.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="hover:text-brand-ink"
                >
                  {{ item.title }}
                </a>
                <span v-else>{{ item.title }}</span>
              </h3>
              <a
                v-if="item.url"
                :href="item.url"
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex shrink-0 items-center gap-1 text-xs text-muted hover:text-brand-ink"
              >
                原文 <ExternalLink :size="12" aria-hidden="true" />
              </a>
            </div>

            <p v-if="item.summary" class="mt-1.5 text-sm leading-relaxed text-ink-2">
              {{ item.summary }}
            </p>

            <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-faint">
              <span v-if="item.source">{{ item.source }}</span>
              <span v-if="item.platform" class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted">
                {{ platformLabel(item.platform) }}
              </span>
              <span v-if="item.metrics_text" class="mono tabular-nums text-muted">{{ item.metrics_text }}</span>
            </div>
          </li>
        </ul>
      </section>

      <nav
        v-if="report.prev_id || report.next_id"
        class="mt-8 flex items-center justify-between border-t border-line pt-4 text-sm font-medium text-brand-ink"
        aria-label="刊期导航"
      >
        <NuxtLink v-if="report.prev_id" :to="`/reports/${report.prev_id}`" class="hover:underline">← 上一期</NuxtLink>
        <span v-else />
        <NuxtLink v-if="report.next_id" :to="`/reports/${report.next_id}`" class="hover:underline">下一期 →</NuxtLink>
      </nav>
    </article>
  </div>
</template>
