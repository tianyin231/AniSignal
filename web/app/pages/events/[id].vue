<script setup lang="ts">
import type { ApiEvent } from '~/composables/usePublicApi'
import { toAppEvent } from '~/utils/public-utils'

const route = useRoute()
const { api } = usePublicApi()
const { data: raw, error } = await useAsyncData(`event-${route.params.id}`, () =>
  api<ApiEvent>(`/api/events/${route.params.id}`),
)
if (error.value || !raw.value)
  throw createError({ statusCode: 404, statusMessage: '事件不存在', fatal: true })

const event = computed(() => toAppEvent(raw.value!))
const detail = computed(() => raw.value!)

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
        <span v-if="event.corrections.length" class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">已更正</span>
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
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">证据链</h2>
      <div class="mt-2">
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
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">修正历史</h2>
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
