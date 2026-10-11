<script setup lang="ts">
import type { ApiEvent } from '~/composables/usePublicApi'
import { toAppEvent } from '~/utils/public-utils'
import { fmtTime } from '~/utils/admin-types'
import { platformMeta, type Platform } from '~/utils/data'

const route = useRoute()
const { api } = usePublicApi()
const { data: raw, error } = await useAsyncData(() => `event-${route.params.id}`, (_app, { signal }) =>
  api<ApiEvent>(`/api/events/${route.params.id}`, { signal }),
)
if (error.value) throw createError({ statusCode: error.value.statusCode || 503, statusMessage: error.value.statusCode === 404 ? '事件不存在' : '事件暂时加载失败，请稍后重试', fatal: true })
if (!raw.value)
  throw createError({ statusCode: 404, statusMessage: '事件不存在', fatal: true })

const event = computed(() => toAppEvent(raw.value!))
const detail = computed(() => raw.value!)
const evidenceView = ref<'timeline' | 'table'>('timeline')

const confidenceClass: Record<string, string> = {
  高: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  中: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  低: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
}

const conflictSources = computed(() =>
  toAppEvent({ ...detail.value, sources: detail.value.conflicts }).sources,
)

useSeoMeta({ title: () => `${event.value.title} · 事件` })
</script>

<template>
  <div class="space-y-4" v-if="event">
    <BackButton fallback="/events" label="返回事件库" />

    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center gap-2">
        <IdentityBadge :identity="event.identity" />
        <span class="chip bg-slate-500/10 text-slate-600 dark:text-zinc-300">{{ event.category }}</span>
        <span v-if="event.important" class="chip bg-red-500/10 font-medium text-red-600 dark:text-red-400">重要</span>
      </div>
      <h1 class="mt-2.5 text-lg font-semibold leading-snug text-slate-900 dark:text-zinc-50">
        {{ event.title }}
      </h1>
      <p class="mono mt-2 text-xs text-slate-400 dark:text-zinc-500">
        首次发现 {{ event.firstSeenAt }} · 最近更新 {{ event.updatedAt }} · 证据 {{ event.evidenceCount }} 条
      </p>
      <div class="mt-3">
        <RegionSyncStrip :regions="event.regions" />
      </div>
    </section>

    <section v-if="detail.facts?.length" class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">关键事实</h2>
      <dl class="mt-3 grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
        <div v-for="f in detail.facts" :key="f.label">
          <dt class="text-xs text-slate-400 dark:text-zinc-500">{{ f.label }}</dt>
          <dd class="mt-0.5 text-sm text-slate-700 dark:text-zinc-200">{{ f.value }}</dd>
        </div>
      </dl>
    </section>

    <section class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">当前结论</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-700 dark:text-zinc-300">
        {{ detail.summary || event.conclusion || '结论整理中' }}
      </p>
      <div class="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span class="chip" :class="confidenceClass[event.confidence]">可信度 {{ event.confidence }}</span>
        <span v-if="event.statusChange" class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">
          状态：{{ event.statusChange }}
        </span>
      </div>
    </section>

    <section class="panel p-4 sm:p-5">
      <div class="flex items-center justify-between gap-2">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">证据链</h2>
        <div class="flex gap-1 text-xs">
          <button
            class="rounded-md px-2 py-1 transition-colors"
            :class="evidenceView === 'timeline' ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
            @click="evidenceView = 'timeline'"
          >
            时间线
          </button>
          <button
            class="rounded-md px-2 py-1 transition-colors"
            :class="evidenceView === 'table' ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
            @click="evidenceView = 'table'"
          >
            表格
          </button>
        </div>
      </div>
      <div v-if="evidenceView === 'timeline'" class="mt-3">
        <ol class="space-y-3 border-l border-slate-200 pl-4 dark:border-zinc-800">
          <li v-for="(s, i) in detail.sources" :key="`${s.content_id}-${i}`" class="relative text-sm">
            <span
              class="absolute -left-[21px] top-1.5 size-2 rounded-full"
              :class="s.identity === 'official' ? 'bg-blue-500' : 'bg-slate-300 dark:bg-zinc-600'"
            />
            <div class="flex flex-wrap items-baseline gap-x-2">
              <span class="mono text-xs text-slate-400 dark:text-zinc-500">{{ fmtTime(s.published_at) }}</span>
              <span class="inline-flex items-center gap-1 text-xs text-slate-500 dark:text-zinc-400">
                <span class="inline-grid size-4 place-items-center rounded-full text-[8px] font-bold text-white" :class="platformMeta[s.platform as Platform]?.badge">
                  {{ platformMeta[s.platform as Platform]?.short || '?' }}
                </span>
                {{ platformMeta[s.platform as Platform]?.label || s.platform }}
              </span>
              <span
                v-if="s.identity === 'official'"
                class="rounded bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-300"
              >官方</span>
              <span v-if="s.account" class="text-xs text-slate-400 dark:text-zinc-500">{{ s.account }}</span>
            </div>
            <p v-if="s.title" class="mt-0.5 leading-relaxed text-slate-700 dark:text-zinc-200">{{ s.title }}</p>
            <div class="mt-1 flex gap-3 text-xs">
              <NuxtLink v-if="s.content_id" :to="`/contents/${s.content_id}`" class="link">站内详情</NuxtLink>
              <a v-if="s.url" :href="s.url" target="_blank" rel="noopener" class="link">原文 ↗</a>
            </div>
          </li>
        </ol>
      </div>
      <div v-else class="mt-2">
        <SourceTable :sources="event.sources" />
      </div>
    </section>

    <section v-if="conflictSources.length" class="panel border-amber-400/40 p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-amber-700 dark:text-amber-300">冲突证据</h2>
      <div class="mt-2">
        <SourceTable :sources="conflictSources" />
      </div>
    </section>

    <section v-if="event.corrections.length" class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">更新记录</h2>
      <ol class="mt-3 space-y-2 border-l border-slate-200 pl-4 dark:border-zinc-800">
        <li v-for="(c, i) in event.corrections" :key="i" class="relative text-sm">
          <span class="absolute -left-[21px] top-2 size-1.5 rounded-full bg-blue-400" />
          <span class="mono text-xs text-slate-400">{{ c.at }}</span>
          <p class="mt-0.5 leading-relaxed text-slate-600 dark:text-zinc-300">{{ c.note }}</p>
        </li>
      </ol>
    </section>

    <SubscribeCta />
  </div>
</template>
