<script setup lang="ts">
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  BadgeCheck,
  GitCompareArrows,
  GitFork,
  MessageSquare,
  Sparkles,
  Star,
  ThumbsUp,
} from 'lucide-vue-next'
import { platformMeta, type RawItem } from '~/utils/data'
import {
  formatCompactStars,
  languageColor,
  periodGainLabel,
  periodShortLabel,
  splitRepoName,
  trendingStatusBadge,
  type TrendingEntry,
  type TrendingPeriod,
} from '~/utils/git-trending'
import { imageSrcset, imageUrl } from '~/utils/image'
import { CONTENT_TYPE_LABELS } from '~/utils/lexicon'
import { revisionBadgeLabel } from '~/utils/content-diff'
import { isRedundantExcerpt } from '~/utils/text'

/**
 * 统一主题流内容卡片：同时承载「全部收录」图文/项目卡片与 GitHub Trending 排名卡片。
 */
const props = defineProps<{
  item: RawItem
  activeTopic?: string
  trending?: { entry: TrendingEntry; period: TrendingPeriod }
}>()

const emit = defineEmits<{
  selectTopic: [topic: string]
}>()

const { mediaMode } = useSiteConfig()
const imageMode = computed(() => mediaMode(props.item.platform, 'image'))
const failedUrls = ref<string[]>([])

const isGithub = computed(() => props.item.platform === 'github')
const isGitRepo = computed(() => isGithub.value && props.item.contentType === 'repo')
const isXiaoheihe = computed(() => props.item.platform === 'xiaoheihe')

const externalOnlyJump = computed(() => {
  if (props.trending) return !props.trending.entry.raw_content_id
  return isXiaoheihe.value && props.item.previewMode === 'external_only' && Boolean(props.item.originalUrl)
})
const detailTo = computed(() => (externalOnlyJump.value ? (props.item.originalUrl || '#') : `/contents/${props.item.id}`))

const platform = computed(() => platformMeta[props.item.platform] ?? { label: props.item.platform, short: '?', badge: 'bg-slate-500' })
const typeBadgeLabel = computed(() => (isGithub.value && props.item.contentType ? (CONTENT_TYPE_LABELS[props.item.contentType] ?? '') : ''))
const revisionLabel = computed(() => revisionBadgeLabel(props.item.revisionCount, props.item.hasSourceRevision))

const visibleImages = computed(() => {
  if (props.trending || imageMode.value !== 'show') return []
  const list = props.item.mediaList?.length ? props.item.mediaList : (props.item.cover ? [props.item.cover] : [])
  return list.filter(url => !failedUrls.value.includes(url)).slice(0, 3)
})
const extraImageCount = computed(() => Math.max(0, (props.item.mediaCount ?? visibleImages.value.length) - visibleImages.value.length))

const excerpt = computed(() => {
  const text = props.item.excerpt?.replace(/\s+/g, ' ').trim() ?? ''
  return isRedundantExcerpt(props.item.title, text) ? '' : text
})

const viewCount = computed(() => Number(props.item.metrics?.view ?? 0))
const displayTopics = computed(() => (props.item.topics ?? []).slice(0, 4))

const trendingBadge = computed(() => (props.trending ? trendingStatusBadge(props.trending.entry, props.trending.period) : null))
const repoParts = computed(() => (props.trending ? splitRepoName(props.trending.entry.repo) : null))
const trendingToneClass: Record<'new' | 'streak' | 'returning', string> = {
  new: 'bg-emerald-500/12 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300',
  streak: 'bg-amber-500/12 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300',
  returning: 'bg-sky-500/12 text-sky-700 dark:bg-sky-400/15 dark:text-sky-300',
}

function markFailed(url: string) {
  if (!failedUrls.value.includes(url)) failedUrls.value = [...failedUrls.value, url]
}
</script>

<template>
  <article class="panel group overflow-hidden transition-colors hover:border-brand/35">
    <NuxtLink
      :to="detailTo"
      :external="externalOnlyJump"
      :target="externalOnlyJump ? '_blank' : undefined"
      class="flex min-w-0 items-start gap-3.5 p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand sm:gap-4 sm:p-5"
    >
      <!-- 可选排名列（Trending 刊物模式） -->
      <div v-if="trending" class="flex w-9 shrink-0 flex-col items-center pt-0.5 text-center">
        <span class="num text-lg font-bold leading-none tabular-nums" :class="trending.entry.rank <= 3 ? 'text-accent-ink' : 'text-faint'">
          {{ String(trending.entry.rank).padStart(2, '0') }}
        </span>
        <span
          v-if="trending.entry.rank_delta !== null && trending.entry.rank_delta !== 0"
          class="num mt-1 inline-flex items-center text-[11px] font-semibold tabular-nums"
          :class="trending.entry.rank_delta > 0 ? 'text-ok' : 'text-faint'"
          :title="trending.entry.rank_delta > 0 ? `较上期上升 ${trending.entry.rank_delta} 位` : `较上期下降 ${Math.abs(trending.entry.rank_delta)} 位`"
        >
          <ArrowUp v-if="trending.entry.rank_delta > 0" :size="10" aria-hidden="true" />
          <ArrowDown v-else :size="10" aria-hidden="true" />
          {{ Math.abs(trending.entry.rank_delta) }}
        </span>
      </div>

      <div class="min-w-0 flex-1">
        <!-- 头部元信息：Trending 排名徽章 或 常规平台来源 -->
        <div v-if="trending && repoParts && trendingBadge" class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span class="inline-flex min-w-0 max-w-full items-baseline gap-1 text-[15px] leading-snug text-ink transition-colors group-hover:text-brand-ink">
            <span v-if="repoParts.owner" class="mono truncate text-xs text-muted">{{ repoParts.owner }} /</span>
            <span class="truncate font-bold">{{ repoParts.name }}</span>
          </span>
          <span class="inline-flex h-5 items-center rounded-md px-1.5 text-[11px] font-semibold" :class="trendingToneClass[trendingBadge.tone]">
            {{ trendingBadge.label }}
          </span>
          <span v-for="other in trending.entry.also_in" :key="other" class="inline-flex h-5 items-center rounded-md bg-chip px-1.5 text-[11px] text-ink-2">
            同步进{{ periodShortLabel(other) }}
          </span>
          <span v-if="trending.entry.period_stars > 0" class="num ml-auto hidden shrink-0 font-bold text-ok sm:inline">
            +{{ trending.entry.period_stars.toLocaleString('zh-CN') }} {{ periodGainLabel(trending.period) }}
          </span>
        </div>

        <div v-else class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span class="grid size-5 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white" :class="platform.badge" aria-hidden="true">{{ platform.short }}</span>
          <span class="max-w-[14rem] truncate font-medium text-ink-2">{{ item.account }}</span>
          <span v-if="typeBadgeLabel" class="rounded-md bg-chip px-1.5 py-0.5 text-[11px] font-medium text-ink-2">{{ typeBadgeLabel }}</span>
          <span v-if="item.identity === 'official'" class="inline-flex h-5 items-center gap-0.5 rounded-md bg-brand-soft px-1.5 text-[11px] font-semibold text-brand-ink">
            <BadgeCheck :size="11" aria-hidden="true" />官方
          </span>
          <span
            v-if="revisionLabel"
            class="inline-flex h-5 items-center gap-0.5 rounded-md px-1.5 text-[11px] font-semibold"
            :class="item.hasSourceRevision ? 'bg-amber-500/12 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300' : 'bg-chip text-ink-2'"
          >
            <GitCompareArrows :size="11" aria-hidden="true" />{{ revisionLabel }}
          </span>
          <time v-if="item.publishedAt" class="mono ml-auto shrink-0 text-[11px] text-faint">{{ item.publishedAt }}</time>
        </div>

        <!-- 标题 / AI 中文解读 / 正文摘录 + 可选单图或多图画廊 -->
        <div class="mt-2 flex gap-4" :class="visibleImages.length === 1 ? 'items-start' : 'flex-col'">
          <div class="min-w-0 flex-1">
            <h3 v-if="!trending" class="line-clamp-2 text-[16px] font-bold leading-snug text-ink transition-colors group-hover:text-brand-ink">
              {{ item.brief?.headline && isGitRepo ? item.brief.headline : item.title }}
            </h3>
            <p v-if="!trending && item.brief?.headline && isGitRepo" class="mono mt-0.5 truncate text-xs text-muted">
              {{ item.title }}
            </p>
            <p v-if="trending && item.brief?.headline" class="text-[14px] font-semibold leading-snug text-ink">
              <span class="mr-1.5 inline-flex items-center gap-0.5 rounded bg-brand-soft px-1.5 py-0.5 align-middle text-[10px] font-semibold text-brand-ink">
                <Sparkles :size="10" aria-hidden="true" />AI
              </span>
              <span>{{ item.brief.headline }}</span>
            </p>

            <div
              v-if="!trending && item.brief"
              class="mt-2 rounded-xl border border-brand/15 bg-brand-soft/45 px-3 py-2 text-[13px] leading-relaxed text-ink-2"
            >
              <span class="mr-1.5 inline-flex items-center gap-1 rounded bg-brand/12 px-1.5 py-0.5 align-middle text-[10px] font-semibold text-brand-ink">
                <Sparkles :size="10" aria-hidden="true" />AI 解读
              </span>
              <span>{{ item.brief.tagline }}</span>
            </div>
            <p v-else-if="trending && item.brief?.tagline && item.brief.tagline !== item.brief.headline" class="mt-1 line-clamp-2 text-[13px] leading-relaxed text-ink-2">
              {{ item.brief.tagline }}
            </p>
            <p v-else-if="excerpt" class="mt-1.5 line-clamp-2 text-[13px] leading-relaxed text-muted">
              {{ excerpt }}
            </p>
          </div>

          <div v-if="visibleImages.length === 1" class="relative aspect-[16/10] w-28 shrink-0 overflow-hidden rounded-xl bg-chip sm:w-40">
            <img
              :src="imageUrl(visibleImages[0], 320)"
              :srcset="imageSrcset(visibleImages[0], [160, 320])"
              sizes="(min-width: 640px) 160px, 112px"
              alt=""
              referrerpolicy="no-referrer"
              loading="lazy"
              decoding="async"
              class="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
              @error="markFailed(visibleImages[0]!)"
            >
          </div>
          <div v-else-if="visibleImages.length >= 2" class="grid gap-2" :class="visibleImages.length === 2 ? 'grid-cols-2' : 'grid-cols-3'">
            <div v-for="(img, idx) in visibleImages" :key="img" class="relative aspect-[16/10] overflow-hidden rounded-xl bg-chip">
              <img
                :src="imageUrl(img, 320)"
                :srcset="imageSrcset(img, [160, 320])"
                sizes="(min-width: 640px) 220px, 33vw"
                alt=""
                referrerpolicy="no-referrer"
                loading="lazy"
                decoding="async"
                class="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
                @error="markFailed(img)"
              >
              <span v-if="idx === visibleImages.length - 1 && extraImageCount > 0" class="num absolute inset-0 grid place-items-center bg-black/45 text-sm font-bold text-white backdrop-blur-[1px]">
                +{{ extraImageCount }}
              </span>
            </div>
          </div>
        </div>

        <!-- 底部指标条 -->
        <div class="mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-xs text-muted">
          <template v-if="isGitRepo">
            <span v-if="trending?.entry.language" class="inline-flex items-center gap-1.5 text-ink-2">
              <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: languageColor(trending.entry.language) }" aria-hidden="true" />
              <span>{{ trending.entry.language }}</span>
            </span>
            <span v-if="viewCount > 0" class="num font-semibold text-ok" :class="trending ? 'sm:hidden' : ''">
              +{{ viewCount.toLocaleString('zh-CN') }} {{ trending ? periodGainLabel(trending.period) : '新增星标' }}
            </span>
            <span v-if="item.metrics?.like" class="num inline-flex items-center gap-1 tabular-nums">
              <Star :size="12" class="text-faint" aria-hidden="true" />{{ formatCompactStars(item.metrics.like) }}
            </span>
            <span v-if="item.metrics?.fork" class="num inline-flex items-center gap-1 tabular-nums">
              <GitFork :size="12" class="text-faint" aria-hidden="true" />{{ formatCompactStars(item.metrics.fork) }}
            </span>
            <span v-if="trending && trending.entry.appearances > 1" class="mono text-faint">
              首见 {{ trending.entry.first_seen_key }} · 最高 #{{ trending.entry.best_rank }}
            </span>
          </template>
          <template v-else-if="isXiaoheihe">
            <span v-if="item.metrics?.like != null" class="num inline-flex items-center gap-1 text-ink-2">
              <ThumbsUp :size="12" class="text-faint" aria-hidden="true" />{{ item.metrics.like.toLocaleString('zh-CN') }}
            </span>
            <span v-if="item.metrics?.reply != null" class="num inline-flex items-center gap-1">
              <MessageSquare :size="12" class="text-faint" aria-hidden="true" />{{ item.metrics.reply.toLocaleString('zh-CN') }}
            </span>
            <span v-if="viewCount > 0" class="num">{{ viewCount.toLocaleString('zh-CN') }} 阅读</span>
          </template>
          <template v-else>
            <span v-if="item.metrics?.reply" class="num inline-flex items-center gap-1">
              <MessageSquare :size="12" class="text-faint" aria-hidden="true" />{{ item.metrics.reply.toLocaleString('zh-CN') }}
            </span>
            <span v-if="viewCount > 0" class="num">{{ viewCount.toLocaleString('zh-CN') }} 互动</span>
          </template>
        </div>
      </div>
    </NuxtLink>

    <!-- 话题标签 + 外链快捷条（独立于主 NuxtLink，避免嵌套交互元素） -->
    <div
      v-if="displayTopics.length || (item.originalUrl && !externalOnlyJump)"
      class="flex flex-wrap items-center justify-between gap-2 border-t border-line-soft bg-chip/25 px-4 py-2 text-xs sm:px-5"
    >
      <div class="flex flex-wrap items-center gap-1.5">
        <button
          v-for="t in displayTopics"
          :key="t"
          type="button"
          class="rounded-full px-2.5 py-0.5 text-[11px] transition-colors"
          :class="activeTopic === t ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:bg-line hover:text-ink'"
          @click="emit('selectTopic', t)"
        >
          # {{ t }}
        </button>
      </div>
      <a
        v-if="item.originalUrl && !externalOnlyJump"
        :href="item.originalUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-muted transition-colors hover:text-brand-ink"
      >
        <span>{{ isGithub ? platform.label : '原帖' }}</span>
        <ArrowUpRight :size="12" aria-hidden="true" />
      </a>
    </div>
  </article>
</template>
