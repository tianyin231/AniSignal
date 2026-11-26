<script setup lang="ts">
import { ArrowUpRight, BookOpen, Layers, Plus, Sparkles } from 'lucide-vue-next'
import type { FeedOverview } from '~/composables/usePublicApi'
import type { SiteCommunityItem } from '~/composables/useSiteConfig'
import { communityClasses } from '~/composables/useGameSection'
import { colorToHex } from '~/utils/community-palette'
import { fmtTime } from '~/utils/admin-types'
import { cleanPlatformText, cleanPlatformTitle } from '~/utils/text'
import { formatCompactStars, periodGainLabel, type TrendingEditionsResponse } from '~/utils/git-trending'

/**
 * 通用主题流频道头图（赋能 开源 / 小黑盒 / 所有自定义 domain="feed" 分区）：
 * 复用统一 SignalHero 基座，仅填充主题流业务指标、视图切换与开源榜首快览卡。
 */
const props = defineProps<{
  topic: SiteCommunityItem
  overview?: FeedOverview | null
  gitTrending?: TrendingEditionsResponse | null
  hasGitEditions?: boolean
  showGitDesk?: boolean
  customCreateTo: string
}>()

const emit = defineEmits<{
  switchView: [view: 'editions' | 'feed']
}>()

const accent = computed(() => communityClasses(props.topic.color))
const accentHex = computed(() => colorToHex(props.topic.color))
const heroCover = computed(() => (props.hasGitEditions ? '' : (props.overview?.lead?.cover || '')))
const topTrending = computed(() => props.gitTrending?.current?.entries?.[0] ?? null)
const leadItem = computed(() => props.overview?.lead ?? null)

const leadLink = computed(() => {
  if (props.hasGitEditions && topTrending.value?.raw_content_id) {
    return `/contents/${topTrending.value.raw_content_id}`
  }
  return leadItem.value ? `/contents/${leadItem.value.id}` : ''
})
const leadHeadline = computed(() => {
  if (props.hasGitEditions && topTrending.value) {
    return topTrending.value.brief?.headline || topTrending.value.repo
  }
  return leadItem.value ? (leadItem.value.brief?.headline || cleanPlatformTitle(leadItem.value.title)) : ''
})
const leadSummary = computed(() => {
  if (props.hasGitEditions && topTrending.value) {
    return topTrending.value.brief?.tagline || topTrending.value.description
  }
  if (!leadItem.value) return ''
  return leadItem.value.brief?.tagline || (leadItem.value.excerpt ? cleanPlatformText(leadItem.value.excerpt) : '')
})

const sourcesOk = computed(() => (props.overview?.sources ?? []).filter(s => s.consecutive_failures === 0).length)
const sourcesTotal = computed(() => (props.overview?.sources ?? []).length)
const updatedLabel = computed(() => {
  const ts = props.gitTrending?.current?.captured_at || props.overview?.updated_at
  return ts ? fmtTime(ts) : ''
})
const hasTopAside = computed(() => Boolean(props.hasGitEditions && topTrending.value?.raw_content_id))
</script>

<template>
  <SignalHero
    :aria-label="`${topic.label}频道`"
    :cover="heroCover"
    :accent-hex="accentHex"
    :has-aside="hasTopAside"
  >
    <div class="space-y-3">
      <div class="flex flex-wrap items-center gap-2.5">
        <span class="size-2.5 shrink-0 rounded-full" :class="accent.dot" aria-hidden="true" />
        <p class="num text-xs font-medium uppercase tracking-[0.26em]" :class="accent.text">
          {{ topic.en || topic.label }}
        </p>
        <span
          v-if="overview?.brief_count"
          class="inline-flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-0.5 text-[11px] font-medium text-white/90"
        >
          <Sparkles :size="11" :class="accent.text" aria-hidden="true" />
          AI 中文解读 {{ overview.brief_count }} 项
        </span>
        <span v-if="overview?.stale" class="rounded-full bg-amber-500/20 px-2 py-0.5 text-[11px] font-medium text-amber-300" role="status">
          更新暂缓
        </span>
      </div>

      <div class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 class="text-[32px] font-extrabold leading-none tracking-tight text-white sm:text-[42px]">
          {{ topic.label }}
        </h1>
        <time v-if="updatedLabel" class="mono text-xs text-white/50">更新于 {{ updatedLabel }}</time>
      </div>

      <NuxtLink v-if="leadHeadline && leadLink" :to="leadLink" class="group block max-w-2xl">
        <p class="line-clamp-2 text-[15px] font-semibold leading-snug text-white/95 transition-colors group-hover:text-white">
          <span class="mr-1.5 rounded bg-white/12 px-1.5 py-0.5 text-[11px] font-semibold" :class="accent.text">
            {{ hasGitEditions ? '本期榜首' : '焦点动态' }}
          </span>
          {{ leadHeadline }}
        </p>
        <p v-if="leadSummary" class="mt-1 line-clamp-2 text-[13px] leading-relaxed text-white/65">
          {{ leadSummary }}
        </p>
      </NuxtLink>

      <div class="flex flex-wrap items-baseline gap-x-7 gap-y-2 pt-0.5">
        <template v-if="hasGitEditions && gitTrending?.current">
          <p><span class="num text-2xl font-bold sm:text-[28px]" :class="accent.text">{{ gitTrending.current.stats.total }}</span><span class="ml-1.5 text-xs text-white/65">项 · 本期上榜</span></p>
          <p><span class="num text-2xl font-bold text-emerald-400 sm:text-[28px]">{{ gitTrending.current.stats.new_count }}</span><span class="ml-1.5 text-xs text-white/65">项 · 首次上榜</span></p>
          <p><span class="num text-2xl font-bold text-white sm:text-[28px]">{{ overview?.total_count ?? '—' }}</span><span class="ml-1.5 text-xs text-white/65">项 · 站内去重档案</span></p>
        </template>
        <template v-else>
          <p><span class="num text-2xl font-bold sm:text-[28px]" :class="accent.text">{{ overview?.total_count ?? '—' }}</span><span class="ml-1.5 text-xs text-white/65">篇 · 已收录</span></p>
          <p v-if="overview?.topics?.length"><span class="num text-2xl font-bold text-white sm:text-[28px]">{{ overview.topics.length }}</span><span class="ml-1.5 text-xs text-white/65">个 · 活跃话题</span></p>
          <p v-if="sourcesTotal > 0"><span class="num text-2xl font-bold text-emerald-400 sm:text-[28px]">{{ sourcesOk }}<span class="text-white/40">/{{ sourcesTotal }}</span></span><span class="ml-1.5 text-xs text-white/65">来源正常</span></p>
        </template>
      </div>

      <div class="flex flex-wrap items-center gap-2.5 pt-1">
        <div
          v-if="hasGitEditions"
          class="inline-flex rounded-[10px] border border-white/15 bg-white/8 p-[3px]"
          role="tablist"
          aria-label="视图模式"
        >
          <button
            type="button"
            role="tab"
            :aria-selected="showGitDesk"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold transition-colors"
            :class="showGitDesk ? 'bg-white text-[#0c0d11] shadow-sm' : 'text-white/75 hover:text-white'"
            @click="emit('switchView', 'editions')"
          >
            <BookOpen :size="13" aria-hidden="true" />趋势刊物
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="!showGitDesk"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold transition-colors"
            :class="!showGitDesk ? 'bg-white text-[#0c0d11] shadow-sm' : 'text-white/75 hover:text-white'"
            @click="emit('switchView', 'feed')"
          >
            <Layers :size="13" aria-hidden="true" />全部收录
          </button>
        </div>

        <NuxtLink
          :to="customCreateTo"
          class="inline-flex h-9 items-center gap-1.5 rounded-[10px] bg-brand px-3.5 text-xs font-semibold text-white transition-colors hover:bg-brand-ink"
        >
          <Plus :size="14" aria-hidden="true" />定制监控
        </NuxtLink>

        <a
          v-if="!hasGitEditions && leadItem?.url"
          :href="leadItem.url"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex h-9 items-center gap-1 rounded-[10px] border border-white/15 bg-white/5 px-3 text-xs text-white/85 transition-colors hover:bg-white/12 hover:text-white"
        >
          原帖 <ArrowUpRight :size="13" aria-hidden="true" />
        </a>
      </div>
    </div>

    <template v-if="hasTopAside && topTrending" #aside>
      <NuxtLink
        :to="`/contents/${topTrending.raw_content_id}`"
        class="group hidden flex-col justify-between rounded-2xl border border-white/12 bg-white/[0.06] p-4 backdrop-blur-md transition-colors hover:border-white/25 hover:bg-white/[0.09] lg:flex"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-white/70">
            <span class="num text-base font-bold" :class="accent.text">#01</span>
            {{ gitTrending?.current?.period_label }}榜首
          </span>
          <span v-if="topTrending.period_stars > 0" class="num rounded-full bg-emerald-500/18 px-2 py-0.5 text-xs font-bold text-emerald-300">
            +{{ topTrending.period_stars.toLocaleString('zh-CN') }} {{ gitTrending?.active_period ? periodGainLabel(gitTrending.active_period) : '' }}
          </span>
        </div>
        <p class="mono mt-2.5 truncate text-xs text-white/60">{{ topTrending.repo }}</p>
        <p class="mt-1 line-clamp-2 text-sm font-bold leading-snug text-white">
          {{ topTrending.brief?.headline || topTrending.repo }}
        </p>
        <p v-if="topTrending.brief?.tagline || topTrending.description" class="mt-1.5 line-clamp-2 text-xs leading-relaxed text-white/70">
          {{ topTrending.brief?.tagline || topTrending.description }}
        </p>
        <div class="mt-3 flex items-center justify-between border-t border-white/10 pt-2.5 text-[11px] text-white/55">
          <span>{{ topTrending.language || '开源项目' }} · ★ {{ formatCompactStars(topTrending.stars) }}</span>
          <span class="font-medium text-white/85 group-hover:underline">站内深度解读 →</span>
        </div>
      </NuxtLink>
    </template>
  </SignalHero>
</template>
