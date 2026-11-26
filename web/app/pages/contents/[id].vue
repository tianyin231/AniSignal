<script setup lang="ts">
import {
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Compass,
  GitCompareArrows,
  GitFork,
  Sparkles,
  Star,
  TrendingUp,
} from 'lucide-vue-next'
import type { ContentDetail } from '~/composables/usePublicApi'
import { communityMeta } from '~/composables/useGameSection'
import { contentTypeLabels, fmtTime } from '~/utils/admin-types'
import { revisionBadgeLabel } from '~/utils/content-diff'
import { categoryMeta } from '~/utils/public-utils'
import { imageSrcset, imageUrl } from '~/utils/image'
import { identityMeta, platformMeta, type Identity, type Platform } from '~/utils/data'
import { cleanPlatformText, cleanPlatformTitle } from '~/utils/text'
import { formatCompactStars, formatPeriodKey, languageColor, splitRepoName } from '~/utils/git-trending'

const lightboxIndex = ref<number | null>(null)

const route = useRoute()
const { api } = usePublicApi()
const { data: detail, error } = await useAsyncData(() => `content-${route.params.id}`, (_app, { signal }) =>
  api<ContentDetail>(`/api/contents/${route.params.id}`, { signal }),
)
if (error.value) throw createError({ statusCode: error.value.statusCode || 503, statusMessage: error.value.statusCode === 404 ? '内容不存在' : '内容暂时加载失败，请稍后重试', fatal: true })
if (!detail.value)
  throw createError({ statusCode: 404, statusMessage: '内容不存在', fatal: true })
// 顶栏高亮内容所属分区，而不是上次浏览的分区
usePageCommunity(() => detail.value?.community)

const d = computed(() => detail.value!)
const commMeta = computed(() => communityMeta(d.value.community))
const isGitPlatform = computed(() => d.value.platform === 'github' || d.value.platform === 'gitcode')
const isGitRepo = computed(() => isGitPlatform.value && d.value.content_type === 'repo')
const isGitRelease = computed(() => isGitPlatform.value && d.value.content_type === 'release')
const isXiaoheihe = computed(() => d.value.platform === 'xiaoheihe')

const backFallback = computed(() => {
  if (d.value.community === 'ai') return '/ai'
  if (d.value.community) return `/contents?community=${encodeURIComponent(d.value.community)}`
  return '/contents'
})
const backLabel = computed(() => {
  if (d.value.community === 'ai') return '返回 AI 快讯'
  if (commMeta.value?.domain === 'feed') return `返回 ${commMeta.value.label}`
  return '返回内容库'
})

const displayTitle = computed(() => {
  if (d.value.news?.headline) return d.value.news.headline
  return cleanPlatformTitle(d.value.title || d.value.external_id)
})
const repoParts = computed(() => (isGitRepo.value ? splitRepoName(d.value.repo_meta?.repo || d.value.title || '') : null))

const diffRef = ref<{ expandAndLoad: () => void } | null>(null)
const revisionLabel = computed(() => revisionBadgeLabel(d.value.revision_count, d.value.has_source_revision))
const openRevisionsInitially = computed(() => Boolean(d.value.has_source_revision) || route.hash === '#revisions')
function jumpToRevisions() {
  diffRef.value?.expandAndLoad()
  nextTick(() => {
    document.getElementById('revisions')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

const { mediaMode } = useSiteConfig()
const videoMode = computed(() => mediaMode(d.value.platform, 'video'))
const imageMode = computed(() => mediaMode(d.value.platform, 'image'))
const player = computed(() => {
  if (videoMode.value !== 'show') return null
  if (d.value.platform === 'bilibili' && d.value.content_type === 'video' && d.value.external_id.startsWith('BV')) {
    return {
      url: `https://player.bilibili.com/player.html?bvid=${encodeURIComponent(d.value.external_id)}&autoplay=0&danmaku=0`,
      title: 'B 站视频预览',
      note: '播放器由 B 站提供；若视频禁止外链播放，请使用原文链接。',
    }
  }
  if (d.value.platform === 'youtube' && /^[\w-]{11}$/.test(d.value.external_id)) {
    return {
      url: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(d.value.external_id)}`,
      title: 'YouTube 视频预览',
      note: '播放器由 YouTube 隐私增强模式提供；若视频限制嵌入，请使用原文链接。',
    }
  }
  return null
})

const previewLabel = computed(() => ({
  full: '正文可预览',
  partial: '图片可预览',
  external_only: '仅平台原文可读',
})[d.value.preview_mode])

// 按平台与内容类型适配指标中文标签（彻底避免开源仓库被标成「播放 / 点赞 / fork」）
const metricLabelMap = computed<Record<string, string>>(() => {
  if (isGitRepo.value) {
    return { view: '本期新增星标', like: '总星标', fork: 'Fork', reply: '评论' }
  }
  if (isGitPlatform.value) {
    return { like: '回应', reply: '评论', view: '互动', fork: 'Fork' }
  }
  if (isXiaoheihe.value) {
    return { view: '阅读', like: '点赞', reply: '评论', favorite: '收藏', share: '分享' }
  }
  return {
    view: '播放', like: '点赞', reply: '评论', favorite: '收藏', coin: '投币',
    share: '分享', danmaku: '弹幕', forward: '转发',
  }
})
const metricRows = computed(() =>
  Object.entries(d.value.metrics ?? {}).map(([k, v]) => ({
    key: k,
    label: metricLabelMap.value[k] ?? k,
    value: Number(v),
  })),
)

const lightboxUrl = computed(() => (lightboxIndex.value !== null ? (d.value.media[lightboxIndex.value] ?? null) : null))
function openLightboxByUrl(url: string) {
  const idx = d.value.media.indexOf(url)
  lightboxIndex.value = idx >= 0 ? idx : 0
}
function stepLightbox(delta: number) {
  if (lightboxIndex.value === null || !d.value.media.length) return
  lightboxIndex.value = (lightboxIndex.value + delta + d.value.media.length) % d.value.media.length
}
function onKeydown(e: KeyboardEvent) {
  if (lightboxIndex.value === null) return
  if (e.key === 'Escape') lightboxIndex.value = null
  else if (e.key === 'ArrowLeft') stepLightbox(-1)
  else if (e.key === 'ArrowRight') stepLightbox(1)
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

useSeoMeta({
  title: () => `${d.value.brief?.headline || displayTitle.value.slice(0, 40)} · 站内详情`,
  description: () => (d.value.brief?.tagline || d.value.news?.summary || d.value.text || '').replace(/\s+/g, ' ').slice(0, 140) || undefined,
})
</script>

<template>
  <div class="space-y-4">
    <BackButton :fallback="backFallback" :label="backLabel" />

    <!-- 头部总览卡 -->
    <section class="panel p-4 sm:p-6">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="inline-flex items-center gap-1.5">
          <span class="grid size-4 place-items-center rounded-full text-[8px] font-bold text-white" :class="platformMeta[d.platform as Platform]?.badge">
            {{ platformMeta[d.platform as Platform]?.short }}
          </span>
          <span class="font-medium text-ink-2">{{ platformMeta[d.platform as Platform]?.label ?? d.platform }}</span>
        </span>
        <span class="chip bg-chip text-muted">{{ contentTypeLabels[d.content_type] ?? d.content_type }}</span>
        <span v-if="!isGitRepo && d.identity !== 'unknown'" :class="identityMeta[d.identity as Identity]?.text">{{ identityMeta[d.identity as Identity]?.label ?? d.identity }}</span>
        <span v-if="d.repo_meta?.language" class="inline-flex items-center gap-1.5 rounded-md bg-chip px-2 py-0.5 font-medium text-ink-2">
          <span class="size-2 rounded-full" :style="{ backgroundColor: languageColor(d.repo_meta.language) }" aria-hidden="true" />
          {{ d.repo_meta.language }}
        </span>
        <span v-if="d.repo_meta?.tag_name" class="mono rounded-md bg-brand-soft px-2 py-0.5 font-semibold text-brand-ink">
          {{ d.repo_meta.tag_name }}
        </span>
        <span v-if="d.repo_meta?.prerelease" class="rounded-md bg-amber-500/12 px-1.5 py-0.5 font-medium text-amber-700 dark:text-amber-300">预发布</span>
        <span v-if="d.news" class="inline-flex h-5 items-center rounded-md px-1.5 font-semibold" :class="d.news.kind === 'rumor' ? 'bg-warn/10 text-warn' : 'bg-ai-soft text-ai-ink'">
          {{ d.news.kind === 'rumor' ? '传闻线索' : '报道' }}
        </span>
        <span v-if="!isGitRepo" class="chip" :class="d.preview_mode === 'external_only' ? 'bg-amber-500/12 text-amber-700 dark:text-amber-300' : 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-300'">
          {{ previewLabel }}
        </span>

        <button
          v-if="revisionLabel"
          type="button"
          class="mono ml-auto inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs font-semibold transition-colors"
          :class="d.has_source_revision ? 'bg-amber-500/12 text-amber-700 hover:bg-amber-500/20 dark:bg-amber-400/15 dark:text-amber-300' : 'bg-chip text-ink-2 hover:bg-line'"
          @click="jumpToRevisions"
        >
          <GitCompareArrows :size="12" aria-hidden="true" />
          {{ revisionLabel }}<template v-if="d.version"> · v{{ d.version }}</template>
        </button>
        <span v-else-if="d.version" class="mono ml-auto text-faint">v{{ d.version }}</span>
      </div>

      <!-- 标题区（开源仓库显示中文定位主标题 + owner/repo 副标；其他内容显示清洗后标题） -->
      <div class="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0 flex-1">
          <template v-if="isGitRepo">
            <h1 class="text-2xl font-bold leading-snug text-ink sm:text-[26px]">
              {{ d.brief?.headline || displayTitle }}
            </h1>
            <p class="mono mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
              <span v-if="repoParts">
                <span class="text-faint">{{ repoParts.owner }} / </span>
                <strong class="font-semibold text-ink-2">{{ repoParts.name }}</strong>
              </span>
              <span v-if="d.metrics?.like" class="num inline-flex items-center gap-1 text-xs text-ink-2">
                <Star :size="13" class="text-amber-500" aria-hidden="true" />{{ formatCompactStars(d.metrics.like) }}
              </span>
              <span v-if="d.metrics?.fork" class="num inline-flex items-center gap-1 text-xs">
                <GitFork :size="13" class="text-faint" aria-hidden="true" />{{ formatCompactStars(d.metrics.fork) }}
              </span>
              <span v-if="d.metrics?.view" class="num text-xs font-semibold text-ok">
                +{{ Number(d.metrics.view).toLocaleString('zh-CN') }} 新增星标
              </span>
            </p>
          </template>
          <template v-else>
            <h1 class="text-2xl font-bold leading-snug text-ink">{{ displayTitle }}</h1>
            <p v-if="d.news" class="mt-2 text-sm leading-relaxed text-ink-2">{{ d.news.summary }}</p>
            <p v-if="d.news && d.news.headline !== d.title" class="mt-2 text-xs text-muted">原标题 {{ d.title }}</p>
          </template>
        </div>

        <!-- 头部主外链操作（开源项目/发版/原帖直达） -->
        <a
          v-if="d.url"
          :href="d.url"
          target="_blank"
          rel="noopener noreferrer"
          class="btn-primary inline-flex h-9 shrink-0 items-center gap-1.5 px-4 text-xs"
        >
          <span>前往 {{ platformMeta[d.platform as Platform]?.label ?? '原文' }}</span>
          <ArrowUpRight :size="14" aria-hidden="true" />
        </a>
      </div>

      <!-- 话题标签 + 关键词 -->
      <div v-if="d.topics?.length || d.matched_keywords?.length" class="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
        <NuxtLink
          v-for="t in d.topics ?? []"
          :key="t"
          :to="commMeta?.domain === 'feed' ? { path: '/contents', query: { community: d.community, view: isGitPlatform ? 'feed' : undefined, topic: t } } : { path: '/contents', query: { community: d.community, q: t } }"
          class="rounded-full bg-chip px-2.5 py-0.5 font-medium text-ink-2 transition-colors hover:bg-brand-soft hover:text-brand-ink"
        >
          # {{ t }}
        </NuxtLink>
        <span
          v-for="k in d.matched_keywords"
          :key="k"
          class="rounded-md bg-brand-soft px-2 py-0.5 text-brand-ink"
        >
          {{ k }}
        </span>
      </div>

      <p class="mono mt-3 text-xs text-faint">
        {{ d.source_name ?? '—' }} · {{ d.content_type === 'video' ? '视频发布' : isGitRepo ? '收录于' : '发布' }}
        {{ d.published_at ? fmtTime(d.published_at) : '时间未知' }}
        <template v-if="d.community !== 'ai' && !isGitRepo"> · 采集 {{ fmtTime(d.collected_at) }}</template>
      </p>
    </section>

    <!-- AI 中文深度解读卡（核心首屏） -->
    <section
      v-if="d.brief"
      class="panel border-brand/25 bg-gradient-to-br from-brand-soft/45 via-card to-card p-5 sm:p-6"
    >
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-brand-ink">
          <Sparkles :size="15" aria-hidden="true" />
          {{ isGitRepo ? 'AI 项目中文解读' : isGitRelease ? 'AI 版本更新摘要' : 'AI 中文解读' }}
        </h2>
        <span v-if="d.brief.model" class="mono text-[11px] text-faint">{{ d.brief.model }}</span>
      </div>

      <p class="mt-3 text-[16px] font-semibold leading-relaxed text-ink">
        {{ d.brief.tagline }}
      </p>
      <p v-if="d.brief.summary && d.brief.summary !== d.brief.tagline" class="mt-2.5 text-[14px] leading-relaxed text-ink-2">
        {{ d.brief.summary }}
      </p>

      <div v-if="d.brief.highlights?.length || d.brief.use_cases?.length" class="mt-4 grid gap-4 border-t border-line-soft pt-4 md:grid-cols-2">
        <div v-if="d.brief.highlights?.length">
          <h3 class="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
            <CheckCircle2 :size="13" class="text-ok" aria-hidden="true" />
            {{ isGitRelease ? '关键变更' : '核心亮点' }}
          </h3>
          <ul class="mt-2 space-y-1.5 text-[13px] leading-relaxed text-ink-2">
            <li v-for="hl in d.brief.highlights" :key="hl" class="flex items-start gap-2">
              <span class="mt-2 size-1.5 shrink-0 rounded-full bg-brand" aria-hidden="true" />
              <span>{{ hl }}</span>
            </li>
          </ul>
        </div>
        <div v-if="d.brief.use_cases?.length">
          <h3 class="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
            <Compass :size="13" class="text-accent-ink" aria-hidden="true" />
            适用场景
          </h3>
          <ul class="mt-2 space-y-1.5 text-[13px] leading-relaxed text-ink-2">
            <li v-for="uc in d.brief.use_cases" :key="uc" class="flex items-start gap-2">
              <span class="mt-2 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
              <span>{{ uc }}</span>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <ContentVersionDiff
      v-if="(d.revision_count ?? 0) > 0"
      ref="diffRef"
      :content-id="d.id"
      :revision-count="d.revision_count ?? 0"
      :has-source-revision="Boolean(d.has_source_revision)"
      :image-mode="imageMode"
      :initial-open="openRevisionsInitially"
      @open-image="openLightboxByUrl"
    />

    <div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <!-- 嵌入视频播放器 -->
      <section v-if="player" class="panel overflow-hidden lg:col-span-2">
        <div class="aspect-video bg-black">
          <iframe
            :src="player.url"
            :title="player.title"
            class="size-full"
            loading="lazy"
            scrolling="no"
            frameborder="0"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowfullscreen
          />
        </div>
        <p class="px-4 py-2.5 text-xs text-faint">{{ player.note }}</p>
      </section>

      <!-- 左侧主阅读区 -->
      <div class="min-w-0 space-y-4">
        <!-- 开源项目：原始简介 + 匿名抓取的 README 站内精读 -->
        <template v-if="isGitRepo">
          <section v-if="d.text" class="panel p-4 sm:p-5">
            <h2 class="text-sm font-bold text-ink">GitHub 原始简介</h2>
            <p class="mt-2 text-[14px] leading-relaxed text-ink-2">{{ d.text }}</p>
          </section>

          <section v-if="d.repo_meta?.readme" class="panel p-4 sm:p-6">
            <div class="flex items-center justify-between gap-2 border-b border-line-soft pb-3">
              <h2 class="inline-flex items-center gap-1.5 text-base font-bold text-ink">
                <BookOpen :size="16" class="text-accent-ink" aria-hidden="true" />
                项目 README 精读
              </h2>
              <span class="text-xs text-faint">站内纯净排版</span>
            </div>
            <MarkdownBlocks
              class="mt-4"
              :source="d.repo_meta.readme"
              :collapse-after="18"
              expand-label="展开 README"
              collapse-label="收起 README"
            />
          </section>
        </template>

        <!-- 非仓库内容：正文解析预览（发版支持 Markdown 排版，普通帖文支持 HTML / 纯文本） -->
        <section v-else class="panel p-4 sm:p-6">
          <h2 class="text-base font-bold text-ink">{{ isGitRelease ? '发布说明' : '内容解析' }}</h2>
          <p v-if="d.text_truncated" class="mt-3 text-sm text-warn">当前仅取得部分原文，完整内容请查看来源。</p>
          <div
            v-if="d.text_html"
            class="prose-sm mt-3 max-w-none text-[14px] leading-relaxed text-ink-2 [&_a]:text-brand-ink [&_a]:underline"
            v-html="d.text_html"
          />
          <MarkdownBlocks v-else-if="isGitRelease && d.text" class="mt-3" :source="d.text" />
          <p v-else-if="d.text" class="mt-3 whitespace-pre-wrap text-[14px] leading-relaxed text-ink-2">{{ isXiaoheihe ? cleanPlatformText(d.text) : d.text }}</p>
          <div v-else-if="d.preview_mode === 'external_only' && d.url" class="mt-3 rounded-xl border border-dashed border-warn/35 bg-warn/5 p-4">
            <p class="text-sm leading-relaxed text-muted">
              本轮未能从平台公开详情取得可预览正文或图片，站内已保留标题、时间与互动数据；完整内容请查看原文。
            </p>
            <a :href="d.url" target="_blank" rel="noopener noreferrer" class="btn-primary mt-3 inline-flex h-9 items-center gap-1 px-4 text-xs">查看原文 ↗</a>
          </div>
          <p v-else-if="d.platform === 'bilibili' && d.content_type === 'dynamic' && d.media.length" class="mt-3 text-sm leading-relaxed text-muted">
            该动态暂只取得图片，匿名公开详情未返回可解析配文。图片可在站内放大查看，也可通过右侧原文核对。
          </p>
          <p v-else class="mt-3 text-sm text-faint">暂无可解析的正文。</p>
        </section>

        <!-- 机翻（外语公告/视频） -->
        <section v-if="d.translation" class="panel p-4 sm:p-5">
          <h2 class="flex items-center gap-2 text-base font-bold text-ink">机翻（简体中文）</h2>
          <p class="mt-1.5 text-xs text-faint">模型 {{ d.translation.model }} 生成，仅供参考，以原文为准。</p>
          <p class="mt-2.5 whitespace-pre-wrap text-[14px] leading-relaxed text-ink-2">{{ d.translation.text }}</p>
        </section>

        <!-- 媒体画廊（自适应列数 + 灯箱左右切换） -->
        <section v-if="d.media.length && imageMode !== 'hide'" class="panel p-4 sm:p-5">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-bold text-ink">媒体画廊（{{ d.media.length }}）</h2>
            <span v-if="imageMode === 'show'" class="text-xs text-faint">点击放大 · 支持方向键切换</span>
          </div>
          <div
            class="mt-3 grid gap-2.5"
            :class="d.media.length === 1 ? 'grid-cols-1 max-w-xl' : d.media.length === 2 ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3'"
          >
            <template v-for="(m, idx) in d.media" :key="m">
              <button
                v-if="imageMode === 'show'"
                type="button"
                :aria-label="`查看第 ${idx + 1} 张大图`"
                class="group relative overflow-hidden rounded-xl border border-line bg-chip"
                @click="lightboxIndex = idx"
              >
                <img
                  :src="imageUrl(m, 640)"
                  :srcset="imageSrcset(m, [320, 640])"
                  sizes="(min-width: 640px) 33vw, 50vw"
                  alt=""
                  referrerpolicy="no-referrer"
                  loading="lazy"
                  decoding="async"
                  class="aspect-[16/10] w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
                >
              </button>
              <a
                v-else
                :href="m"
                target="_blank"
                rel="noopener noreferrer"
                class="block truncate rounded-lg border border-line px-3 py-2 text-xs text-muted hover:text-brand-ink"
              >
                图片链接：{{ m }}
              </a>
            </template>
          </div>
        </section>

        <!-- 指标历史 -->
        <section v-if="metricRows.length" class="panel p-4 sm:p-5">
          <h2 class="text-base font-bold text-ink">{{ isGitRepo ? '仓库指标' : '互动指标' }}</h2>
          <div class="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-3">
            <div v-for="m in metricRows" :key="m.key" class="rounded-xl bg-chip/65 px-3.5 py-2.5">
              <p class="text-xs text-muted">{{ m.label }}</p>
              <p class="num mt-1 text-lg font-bold tabular-nums text-ink">{{ m.value.toLocaleString('zh-CN') }}</p>
            </div>
          </div>
          <details v-if="d.metrics_history.length > 1" class="mt-3">
            <summary class="cursor-pointer text-xs text-muted hover:text-ink">
              采集快照历史（{{ d.metrics_history.length }} 次）
            </summary>
            <div class="mono mt-2 space-y-1 text-xs text-faint">
              <p v-for="h in [...d.metrics_history].reverse()" :key="h.captured_at">
                {{ fmtTime(h.captured_at) }} ·
                <span v-for="(v, k) in h.metrics" :key="k" class="mr-2">{{ metricLabelMap[k] ?? k }} {{ Number(v).toLocaleString('zh-CN') }}</span>
              </p>
            </div>
          </details>
        </section>
      </div>

      <!-- 右侧栏 -->
      <aside class="space-y-4 lg:sticky lg:top-[88px]">
        <!-- 开源仓库上榜轨迹 -->
        <section v-if="d.repo_meta?.trending_history?.length" class="panel p-4">
          <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
            <TrendingUp :size="14" class="text-accent-ink" aria-hidden="true" />
            趋势上榜轨迹
          </h2>
          <ul class="mt-3 space-y-2">
            <li v-for="item in d.repo_meta.trending_history" :key="item.period">
              <NuxtLink
                :to="{ path: '/contents', query: { community: d.community, period: item.period, issue: item.latest_key } }"
                class="group block rounded-xl border border-line-soft bg-chip/35 p-3 transition-colors hover:border-brand/35 hover:bg-brand-soft/40"
              >
                <div class="flex items-center justify-between text-xs">
                  <span class="font-semibold text-ink group-hover:text-brand-ink">{{ item.period_label }}</span>
                  <span class="num font-bold text-accent-ink">最高 #{{ item.best_rank }}</span>
                </div>
                <p class="mono mt-1 text-[11px] text-muted">
                  累计上榜 {{ item.appearances }} 期 · 最近 {{ formatPeriodKey(item.period, item.latest_key) }}
                </p>
              </NuxtLink>
            </li>
          </ul>
        </section>

        <!-- 关联事件 -->
        <section v-if="d.event" class="panel p-4">
          <h2 class="text-sm font-bold text-ink">关联事件</h2>
          <NuxtLink :to="`/events/${d.event.id}`" class="group mt-2.5 block rounded-xl border border-line p-3 transition-colors hover:bg-chip/50">
            <p class="text-sm font-medium text-ink transition-colors group-hover:text-brand-ink">{{ d.event.title }}</p>
            <p class="mt-1.5 flex items-center gap-2 text-xs text-muted">
              <span class="chip bg-brand-soft text-brand-ink">{{ categoryMeta[d.event.category]?.label ?? d.event.category }}</span>
              可信度 {{ d.event.confidence }}
            </p>
          </NuxtLink>
        </section>

        <!-- 原始出处 -->
        <section class="panel p-4">
          <h2 class="text-sm font-bold text-ink">原始出处</h2>
          <a
            v-if="d.url"
            :href="d.url"
            target="_blank"
            rel="noopener noreferrer"
            class="mt-2.5 flex items-center justify-between gap-2 rounded-xl border border-line p-3 text-sm transition-colors hover:border-brand/40 hover:bg-brand-soft/40"
          >
            <span class="truncate font-medium text-ink-2">{{ platformMeta[d.platform as Platform]?.label }} 原文</span>
            <ArrowUpRight :size="15" class="shrink-0 text-muted" />
          </a>
          <p class="mono mt-2.5 break-all text-[11px] text-faint">{{ d.external_id }}</p>
          <p class="mt-2 text-xs leading-relaxed text-faint">
            本页为站内解析与 AI 导读预览，保留原文出处以便随时核对完整内容。
          </p>
        </section>
      </aside>
    </div>
  </div>

  <!-- 图片灯箱：支持左右翻页与 ESC 关闭 -->
  <div
    v-if="lightboxUrl"
    class="fixed inset-0 z-[70] flex items-center justify-center bg-black/85 p-4 backdrop-blur-sm sm:p-6"
    @click.self="lightboxIndex = null"
  >
    <button
      v-if="d.media.length > 1"
      type="button"
      aria-label="上一张"
      class="absolute left-3 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition-colors hover:bg-white/30 sm:left-6"
      @click.stop="stepLightbox(-1)"
    >
      <ChevronLeft :size="20" />
    </button>
    <img :src="lightboxUrl" referrerpolicy="no-referrer" class="max-h-[85vh] max-w-[88vw] rounded-xl object-contain shadow-2xl" alt="媒体预览">
    <button
      v-if="d.media.length > 1"
      type="button"
      aria-label="下一张"
      class="absolute right-3 top-1/2 grid size-10 -translate-y-1/2 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition-colors hover:bg-white/30 sm:right-6"
      @click.stop="stepLightbox(1)"
    >
      <ChevronRight :size="20" />
    </button>
    <div class="absolute right-5 top-5 flex items-center gap-2">
      <span v-if="d.media.length > 1 && lightboxIndex !== null" class="mono rounded-full bg-white/15 px-3 py-1.5 text-xs text-white backdrop-blur">
        {{ lightboxIndex + 1 }} / {{ d.media.length }}
      </span>
      <button
        type="button"
        class="rounded-full bg-white/15 px-3.5 py-1.5 text-sm text-white backdrop-blur transition-colors hover:bg-white/30"
        @click.stop="lightboxIndex = null"
      >
        关闭 ✕
      </button>
    </div>
  </div>
</template>
