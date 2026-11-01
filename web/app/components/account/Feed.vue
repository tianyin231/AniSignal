<script setup lang="ts">
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { FEED_PAGE_SIZE } from '~/composables/useAccountCenter'
import { toRawItem } from '~/utils/public-utils'

// 我的关注流：命中启用关键词的最新内容，可按单个关键词筛选。
const emit = defineEmits<{ addKeyword: [] }>()
const center = useAccountCenter()
const { feed, feedPage, feedKeywordId, feedTotalPages, enabledKeywords, keywords, loading, errors } = center
const selected = computed(() => enabledKeywords.value.find(k => k.id === feedKeywordId.value) ?? null)
const pillClass = (active: boolean) => active ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'
</script>

<template>
  <section class="panel min-w-0 overflow-hidden" aria-labelledby="feed-title">
    <header class="flex flex-wrap items-baseline justify-between gap-2 px-5 pt-4">
      <h2 id="feed-title" class="text-base font-bold text-ink">我的关注流</h2>
      <span v-if="!loading.feed && !errors.feed" class="flex items-center gap-2 text-xs text-muted">
        <span v-if="(feed?.new_today ?? 0) > 0" class="rounded-full bg-ok/10 px-2 py-0.5 font-semibold text-ok">近 24h +{{ feed?.new_today }}</span>
        <span><span class="num font-semibold text-ink-2">{{ (feed?.total ?? 0).toLocaleString('zh-CN') }}</span> 条命中</span>
      </span>
    </header>

    <nav v-if="enabledKeywords.length > 1" class="mt-3 flex gap-1.5 overflow-x-auto px-5 [scrollbar-width:none]" aria-label="按关键词筛选">
      <button type="button" class="h-8 shrink-0 rounded-full px-3 text-[13px] transition-colors" :class="pillClass(!feedKeywordId)" :aria-pressed="!feedKeywordId" @click="center.filterFeed(null)">
        全部
      </button>
      <button
        v-for="k in enabledKeywords"
        :key="k.id"
        type="button"
        class="h-8 shrink-0 rounded-full px-3 text-[13px] transition-colors"
        :class="pillClass(feedKeywordId === k.id)"
        :aria-pressed="feedKeywordId === k.id"
        @click="center.filterFeed(feedKeywordId === k.id ? null : k.id)"
      >
        {{ k.keyword }} <span class="num ml-0.5 text-[11px] opacity-70">{{ k.matches }}</span>
      </button>
    </nav>

    <div class="mt-3 border-t border-line-soft">
      <p v-if="loading.feed && !feed" role="status" class="px-5 py-12 text-center text-sm text-faint">正在加载关注流…</p>
      <div v-else-if="errors.feed" class="px-5 py-8">
        <UiNotice tone="warn">{{ errors.feed }}<button type="button" class="ml-2 font-semibold underline" @click="center.fetchFeed()">重试</button></UiNotice>
      </div>
      <div v-else-if="feed?.items?.length" :class="loading.feed ? 'opacity-60 transition-opacity' : ''">
        <RawFeedRow v-for="item in feed.items" :key="item.id" :item="toRawItem(item)" show-community />
      </div>
      <div v-else class="px-5 py-12 text-center">
        <p class="text-sm text-muted">
          <template v-if="selected">「{{ selected.keyword }}」暂时没有命中内容，系统会持续采集。</template>
          <template v-else-if="enabledKeywords.length">暂时没有命中关键词的内容，系统会持续采集。</template>
          <template v-else-if="keywords.length">关键词都已暂停，启用后这里会出现命中的内容。</template>
          <template v-else>添加关键词后，这里会出现命中的最新内容。</template>
        </p>
        <button v-if="!keywords.length" type="button" class="link mt-2 text-sm font-medium" @click="emit('addKeyword')">添加第一个关键词</button>
      </div>
    </div>

    <footer v-if="!errors.feed && (feed?.total ?? 0) > FEED_PAGE_SIZE" class="flex items-center justify-between gap-3 border-t border-line-soft px-5 py-3 text-xs text-muted">
      <span>每页 {{ FEED_PAGE_SIZE }} 条</span>
      <div class="flex items-center gap-1">
        <button type="button" class="grid size-8 place-items-center rounded-full hover:bg-chip" :disabled="feedPage <= 1 || loading.feed" aria-label="上一页" @click="center.feedGoPage(feedPage - 1)">
          <ChevronLeft :size="16" />
        </button>
        <span class="mono min-w-16 text-center">{{ feedPage }} / {{ feedTotalPages }}</span>
        <button type="button" class="grid size-8 place-items-center rounded-full hover:bg-chip" :disabled="feedPage >= feedTotalPages || loading.feed" aria-label="下一页" @click="center.feedGoPage(feedPage + 1)">
          <ChevronRight :size="16" />
        </button>
      </div>
    </footer>
  </section>
</template>
