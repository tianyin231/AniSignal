<script setup lang="ts">
import { ArrowUpRight, BadgeCheck, ImageIcon } from 'lucide-vue-next'
import { identityMeta, platformMeta, type RawItem } from '~/utils/data'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { imageSrcset, imageUrl } from '~/utils/image'

const props = defineProps<{ item: RawItem; to?: string; showCommunity?: boolean; deltaView?: number | null }>()
const { mediaMode } = useSiteConfig()
const detailTo = computed(() => props.to ?? `/contents/${props.item.id}`)
const coverFailed = ref(false)
watch(() => props.item.cover, () => (coverFailed.value = false))

// 图片展示策略：show=封面图 / link=仅图片链接 / hide=不显示
const imageMode = computed(() => mediaMode(props.item.platform, 'image'))
const showCover = computed(() => !!props.item.cover && !coverFailed.value && imageMode.value === 'show')
const platform = computed(() => platformMeta[props.item.platform] ?? { label: props.item.platform, short: '?', badge: 'bg-slate-500' })

// 分区标记（仅跨分区列表启用，如聚合首页 / 关注流）
const community = computed(() => {
  if (!props.showCommunity) return null
  const meta = communityMeta(props.item.community)
  return meta ? { label: meta.key === 'ai' ? 'AI 快讯' : meta.label, dot: communityClasses(meta.color).dot } : null
})

// 热门标记：播放量达到泛关键词准入门槛（1 万）即视为热门内容
const HOT_VIEWS = 10_000
const viewCount = computed(() => Number(props.item.metrics?.view ?? 0))
const isHot = computed(() => viewCount.value >= HOT_VIEWS)
const viewLabel = computed(() => {
  const n = viewCount.value
  if (!n) return ''
  return n >= 10_000 ? `${(n / 10_000).toFixed(1)}万` : n.toLocaleString('zh-CN')
})
const deltaLabel = computed(() => {
  const n = props.deltaView ?? 0
  if (n <= 0) return ''
  return n >= 10_000 ? `+${(n / 10_000).toFixed(1)}万` : `+${n.toLocaleString('zh-CN')}`
})
// 命中关键词：去掉与分区名 / 账号名重复的词，最多两个
const keywords = computed(() => {
  const skip = new Set([communityMeta(props.item.community)?.label, props.item.account])
  return (props.item.keywords ?? []).filter(k => !skip.has(k)).slice(0, 2)
})
// 有封面时摘要让位给标题；纯文字内容（动态 / X / 公告）保留两行摘要
const excerpt = computed(() => (showCover.value ? '' : props.item.excerpt?.replace(/\s+/g, ' ').trim() ?? ''))
</script>

<template>
  <article class="flex border-b border-line-soft last:border-0">
    <NuxtLink :to="detailTo" class="group flex min-w-0 flex-1 gap-4 px-4 py-3.5 transition-colors hover:bg-line-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand sm:px-5">
      <div v-if="showCover" class="relative aspect-video w-28 shrink-0 overflow-hidden rounded-[10px] bg-chip sm:w-40">
        <img
          :src="imageUrl(item.cover, 320)"
          :srcset="imageSrcset(item.cover, [160, 320])"
          sizes="(min-width: 640px) 160px, 112px"
          alt=""
          referrerpolicy="no-referrer"
          loading="lazy"
          decoding="async"
          class="size-full object-cover"
          @error="coverFailed = true"
        >
        <span class="absolute bottom-1.5 left-1.5 hidden rounded-[5px] px-1.5 py-px text-[10px] font-bold leading-4 text-white sm:block" :class="platform.badge">{{ platform.label }}</span>
      </div>
      <span v-else class="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white" :class="platform.badge" aria-hidden="true">{{ platform.short }}</span>
      <div class="min-w-0 flex-1">
        <p class="line-clamp-2 text-[15px] font-semibold leading-normal text-ink group-hover:text-brand-ink">{{ item.title }}</p>
        <p v-if="excerpt" class="mt-1 line-clamp-2 text-[13px] leading-relaxed text-muted">{{ excerpt }}</p>
        <p v-else-if="item.previewMode === 'external_only'" class="mt-1 text-xs text-amber-700 dark:text-amber-300">
          平台公开接口未返回正文或图片，可直接打开原文核对。
        </p>
        <div class="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted">
          <span v-if="item.identity === 'official'" class="inline-flex h-5 items-center gap-0.5 rounded-md bg-brand-soft px-1.5 font-semibold text-brand-ink">
            <BadgeCheck :size="12" aria-hidden="true" />官方
          </span>
          <span v-else-if="item.identity !== 'unknown'" :class="identityMeta[item.identity]?.text">{{ identityMeta[item.identity]?.label }}</span>
          <span v-if="community" class="inline-flex items-center gap-1.5 text-ink-2">
            <span class="size-[7px] rounded-full" :class="community.dot" aria-hidden="true" />{{ community.label }}
          </span>
          <span class="max-w-[14rem] truncate">{{ showCover ? item.account : `${platform.label} · ${item.account}` }}</span>
          <span class="mono text-faint">{{ item.publishedAt }}</span>
          <span v-if="viewLabel" class="num font-semibold sm:hidden" :class="isHot ? 'text-hot' : 'text-ink-2'">{{ viewLabel }} 播放</span>
          <span v-if="deltaLabel" class="num rounded-full bg-hot/10 px-1.5 py-0.5 text-[11px] font-bold text-hot">{{ deltaLabel }} / 24h</span>
          <span v-if="item.community === 'ai'" class="text-amber-700 dark:text-amber-300">二手消息 · 待核实</span>
          <span v-if="item.translated" class="font-medium text-sky-600 dark:text-sky-400">机翻已译</span>
          <span v-if="item.mergedInto" class="text-brand-ink">已并入事件</span>
          <span v-for="k in keywords" :key="k" class="rounded-md bg-chip px-1.5 py-0.5 text-[11px] text-ink-2">{{ k }}</span>
        </div>
      </div>
      <div v-if="viewLabel" class="hidden w-16 shrink-0 pt-0.5 text-right sm:block">
        <div class="num text-[15px] font-bold" :class="isHot ? 'text-hot' : 'text-ink-2'">{{ viewLabel }}</div>
        <div class="mt-0.5 text-[11px] text-faint">播放</div>
      </div>
    </NuxtLink>
    <!-- 行内操作放在整行链接之外，避免嵌套 <a> -->
    <a
      v-if="item.cover && imageMode === 'link'"
      :href="item.cover"
      target="_blank"
      rel="noopener noreferrer"
      class="mr-3 inline-flex shrink-0 self-center items-center gap-1 rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-2 transition-colors hover:bg-chip"
    >
      <ImageIcon :size="13" aria-hidden="true" />图片
    </a>
    <a
      v-if="item.previewMode === 'external_only' && item.originalUrl"
      :href="item.originalUrl"
      target="_blank"
      rel="noopener"
      class="mr-3 inline-flex shrink-0 self-center items-center gap-1 rounded-lg border border-amber-500/25 px-2.5 py-1.5 text-xs text-amber-700 transition-colors hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 dark:text-amber-300"
    >
      原文 <ArrowUpRight :size="13" />
    </a>
  </article>
</template>
