<script setup lang="ts">
import { ArrowUpRight } from 'lucide-vue-next'
import { identityMeta, platformMeta, regionMeta, type RawItem } from '~/utils/data'

const props = defineProps<{ item: RawItem; to?: string }>()
const detailTo = computed(() => props.to ?? `/contents/${props.item.id}`)
const coverFailed = ref(false)
watch(() => props.item.cover, () => (coverFailed.value = false))

const previewLabel = computed(() => ({
  full: '可站内预览',
  partial: '图片预览',
  external_only: '仅外链可读',
})[props.item.previewMode])
</script>

<template>
  <article class="flex border-b border-slate-900/5 last:border-0 dark:border-white/5">
    <NuxtLink :to="detailTo" class="group flex min-w-0 flex-1 gap-3 px-4 py-3 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 dark:hover:bg-zinc-800/40">
      <span
        class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white"
        :class="platformMeta[item.platform].badge"
      >
        {{ platformMeta[item.platform].short }}
      </span>
      <img
        v-if="item.cover && !coverFailed"
        :src="item.cover"
        alt=""
        referrerpolicy="no-referrer"
        loading="lazy"
        class="hidden aspect-video w-24 shrink-0 rounded-lg border border-slate-900/5 object-cover sm:block dark:border-white/10"
        @error="coverFailed = true"
      >
      <div class="min-w-0 flex-1">
        <div class="flex items-start justify-between gap-3">
          <p class="min-w-0 text-[14px] leading-snug text-slate-800 group-hover:text-blue-700 dark:text-zinc-200 dark:group-hover:text-blue-300">{{ item.title }}</p>
          <span class="mono shrink-0 pt-0.5 text-xs text-slate-400">{{ item.publishedAt }}</span>
        </div>
        <p v-if="item.excerpt" class="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ item.excerpt }}</p>
        <p v-else-if="item.previewMode === 'external_only'" class="mt-1 text-xs text-amber-700 dark:text-amber-300">
          平台公开接口未返回正文或图片，可直接打开原文核对。
        </p>
        <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400 dark:text-zinc-500">
        <span>{{ platformMeta[item.platform].label }} · {{ item.account }}</span>
        <span aria-hidden="true">·</span>
        <span>{{ regionMeta[item.region] }}</span>
        <template v-if="item.category">
          <span aria-hidden="true">·</span>
          <span>{{ item.category }}</span>
        </template>
        <span aria-hidden="true">·</span>
        <span :class="identityMeta[item.identity].text">{{ identityMeta[item.identity].label }}</span>
        <span
          class="rounded px-1.5 py-0.5 text-[11px]"
          :class="item.previewMode === 'external_only' ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300' : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
        >{{ previewLabel }}</span>
        <template v-if="item.translated">
          <span aria-hidden="true">·</span>
          <span class="font-medium text-sky-600 dark:text-sky-400">机翻已译</span>
        </template>
        <template v-if="item.mergedInto">
          <span aria-hidden="true">·</span>
          <NuxtLink :to="`/events/${item.mergedInto}`" class="link">已并入事件 ↗</NuxtLink>
        </template>
        <template v-if="item.keywords?.length">
          <span aria-hidden="true">·</span>
          <span class="inline-flex flex-wrap gap-1">
            <span v-for="k in item.keywords" :key="k" class="rounded bg-blue-500/10 px-1.5 py-0.5 text-[11px] text-blue-700 dark:text-blue-300">{{ k }}</span>
          </span>
        </template>
        </div>
      </div>
    </NuxtLink>
    <a
      v-if="item.previewMode === 'external_only' && item.originalUrl"
      :href="item.originalUrl"
      target="_blank"
      rel="noopener"
      class="mr-3 inline-flex shrink-0 self-center items-center gap-1 rounded-md border border-amber-500/25 px-2.5 py-1.5 text-xs text-amber-700 transition-colors hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 dark:text-amber-300"
    >
      原文 <ArrowUpRight :size="13" />
    </a>
  </article>
</template>
