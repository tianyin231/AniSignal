<script setup lang="ts">
const lightbox = ref<string | null>(null)

import { ArrowUpRight } from 'lucide-vue-next'
import type { ContentDetail } from '~/composables/usePublicApi'
import { contentTypeLabels } from '~/utils/admin-types'
import { fmtTime, categoryMeta } from '~/utils/public-utils'
import { identityMeta, platformMeta, type Identity, type Platform } from '~/utils/data'

const route = useRoute()
const { api } = usePublicApi()
const { data: detail, error } = await useAsyncData(`content-${route.params.id}`, () =>
  api<ContentDetail>(`/api/contents/${route.params.id}`),
)
if (error.value || !detail.value)
  throw createError({ statusCode: 404, statusMessage: '内容不存在', fatal: true })

const d = computed(() => detail.value!)

const METRIC_LABELS: Record<string, string> = {
  view: '播放', like: '点赞', reply: '评论', favorite: '收藏', coin: '投币',
  share: '分享', danmaku: '弹幕', forward: '转发',
}
const metricRows = computed(() =>
  Object.entries(d.value.metrics ?? {}).map(([k, v]) => ({
    key: k,
    label: METRIC_LABELS[k] ?? k,
    value: Number(v),
  })),
)

useSeoMeta({ title: () => `${d.value.title?.slice(0, 40) ?? '内容'} · 站内详情` })
</script>

<template>
  <div class="space-y-4">
    <BackButton fallback="/contents" label="返回内容库" />

    <!-- 头部 -->
    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="inline-flex items-center gap-1.5">
          <span class="grid size-4 place-items-center rounded-full text-[8px] font-bold text-white" :class="platformMeta[d.platform as Platform]?.badge">
            {{ platformMeta[d.platform as Platform]?.short }}
          </span>
          <span class="text-slate-600 dark:text-zinc-300">{{ platformMeta[d.platform as Platform]?.label ?? d.platform }}</span>
        </span>
        <span class="chip bg-slate-500/10 text-slate-500 dark:text-zinc-400">{{ contentTypeLabels[d.content_type] ?? d.content_type }}</span>
        <span :class="identityMeta[d.identity as Identity]?.text">{{ identityMeta[d.identity as Identity]?.label ?? d.identity }}</span>
        <span v-if="d.version" class="mono ml-auto text-slate-400">v{{ d.version }}</span>
      </div>
      <h1 class="mt-2.5 text-lg font-semibold leading-snug">{{ d.title }}</h1>
      <p class="mono mt-2 text-xs text-slate-400 dark:text-zinc-500">
        {{ d.source_name ?? '—' }} · 发布 {{ fmtTime(d.published_at) }} · 采集 {{ fmtTime(d.collected_at) }}<template v-if="d.last_seen_at"> · 最近发现 {{ fmtTime(d.last_seen_at) }}</template>
      </p>
    </section>

    <div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div class="space-y-4">
        <!-- 正文（站内解析预览） -->
        <section class="panel p-4 sm:p-5">
          <h2 class="text-sm font-semibold">内容解析</h2>
          <div
            v-if="d.text_html"
            class="prose-sm mt-3 max-w-none text-[14px] leading-relaxed text-slate-700 dark:text-zinc-300 [&_a]:text-blue-600 [&_a]:underline dark:[&_a]:text-blue-300"
            v-html="d.text_html"
          />
          <p v-else-if="d.text" class="mt-3 whitespace-pre-wrap text-[14px] leading-relaxed text-slate-700 dark:text-zinc-300">{{ d.text }}</p>
          <div v-else-if="d.url" class="mt-3 rounded-xl border border-dashed border-slate-900/15 p-4 dark:border-white/15">
            <p class="text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
              该内容为平台侧不展开正文的类型（B 站部分图文动态的正文与图片无法通过公开接口提取），
              站内已保留标题、时间与互动数据；完整内容请查看原文。
            </p>
            <a :href="d.url" target="_blank" rel="noopener" class="btn-primary mt-3 inline-block px-4 py-2 text-sm">查看原文 ↗</a>
          </div>
          <p v-else class="mt-3 text-sm text-slate-400 dark:text-zinc-500">
            暂无可解析的正文。
          </p>
        </section>

        <!-- 机翻（外语内容） -->
        <section v-if="d.translation" class="panel p-4 sm:p-5">
          <h2 class="flex items-center gap-2 text-sm font-semibold">机翻（简体中文）</h2>
          <p class="mt-2 text-xs text-slate-400 dark:text-zinc-500">模型 {{ d.translation.model }} 生成，仅供参考，以原文为准。</p>
          <p class="mt-2 whitespace-pre-wrap text-[14px] leading-relaxed text-slate-700 dark:text-zinc-300">{{ d.translation.text }}</p>
        </section>

        <!-- 媒体 -->
        <section v-if="d.media.length" class="panel p-4 sm:p-5">
          <h2 class="text-sm font-semibold">媒体（{{ d.media.length }}）</h2>
          <div class="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <button v-for="m in d.media" :key="m" type="button" class="group relative overflow-hidden rounded-lg border border-slate-900/5 dark:border-white/10" @click="lightbox = m">
              <img :src="m" referrerpolicy="no-referrer" loading="lazy" class="aspect-video w-full object-cover transition-opacity group-hover:opacity-90" >
            </button>
          </div>
          <p class="mt-2 text-xs text-slate-400 dark:text-zinc-500">点击图片站内放大预览；图片直链自平台 CDN。</p>
        </section>

        <!-- 指标历史 -->
        <section v-if="metricRows.length" class="panel p-4 sm:p-5">
          <h2 class="text-sm font-semibold">指标</h2>
          <div class="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
            <div v-for="m in metricRows" :key="m.key" class="rounded-lg bg-slate-500/5 px-3 py-2 dark:bg-white/5">
              <p class="text-xs text-slate-500 dark:text-zinc-400">{{ m.label }}</p>
              <p class="mono mt-0.5 text-base font-semibold">{{ m.value.toLocaleString() }}</p>
            </div>
          </div>
          <details v-if="d.metrics_history.length > 1" class="mt-3">
            <summary class="cursor-pointer text-xs text-slate-400 hover:text-slate-600 dark:hover:text-zinc-300">
              采集快照历史（{{ d.metrics_history.length }} 次）
            </summary>
            <div class="mono mt-2 space-y-1 text-xs text-slate-400 dark:text-zinc-500">
              <p v-for="h in [...d.metrics_history].reverse()" :key="h.captured_at">
                {{ fmtTime(h.captured_at) }} ·
                <span v-for="(v, k) in h.metrics" :key="k" class="mr-2">{{ METRIC_LABELS[k] ?? k }} {{ Number(v).toLocaleString() }}</span>
              </p>
            </div>
          </details>
        </section>
      </div>

      <!-- 侧栏 -->
      <div class="space-y-4">
        <section v-if="d.event" class="panel p-4">
          <h2 class="text-sm font-semibold">关联事件</h2>
          <NuxtLink :to="`/events/${d.event.id}`" class="group mt-2.5 block rounded-lg border border-slate-900/5 p-3 transition-colors hover:bg-slate-500/5 dark:border-white/10">
            <p class="text-sm font-medium transition-colors group-hover:text-blue-600 dark:group-hover:text-blue-300">{{ d.event.title }}</p>
            <p class="mt-1 flex items-center gap-2 text-xs text-slate-400">
              <span class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ categoryMeta[d.event.category]?.label ?? d.event.category }}</span>
              可信度 {{ d.event.confidence }}
            </p>
          </NuxtLink>
        </section>

        <section class="panel p-4">
          <h2 class="text-sm font-semibold">原始出处</h2>
          <a
            v-if="d.url"
            :href="d.url"
            target="_blank"
            rel="noopener"
            class="mt-2.5 flex items-center justify-between gap-2 rounded-lg border border-slate-900/5 p-3 text-sm transition-colors hover:bg-slate-500/5 dark:border-white/10"
          >
            <span class="truncate text-slate-600 dark:text-zinc-300">{{ platformMeta[d.platform as Platform]?.label }} 原文</span>
            <ArrowUpRight :size="15" class="shrink-0 text-slate-400" />
          </a>
          <p class="mono mt-2 break-all text-xs text-slate-400 dark:text-zinc-500">{{ d.external_id }}</p>
          <p class="mt-2 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
            本页为站内解析预览，只保存标题、正文、指标与图片直链等必要信息，不镜像完整原始内容。
          </p>
        </section>
      </div>
    </div>
  </div>

  <!-- 图片灯箱：click.self 防误关，flex 居中，ESC 关闭 -->
  <div
    v-if="lightbox"
    class="fixed inset-0 z-[70] flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm"
    @click.self="lightbox = null"
    @keydown.esc="lightbox = null"
  >
    <img :src="lightbox" referrerpolicy="no-referrer" class="max-h-[85vh] max-w-[90vw] rounded-lg object-contain shadow-2xl" alt="媒体预览">
    <button
      type="button"
      class="absolute right-5 top-5 rounded-full bg-white/15 px-3.5 py-1.5 text-sm text-white backdrop-blur transition-colors hover:bg-white/30"
      @click.stop="lightbox = null"
    >
      关闭 ✕
    </button>
  </div>
</template>
