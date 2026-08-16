<script setup lang="ts">
import { getEvent } from '~/utils/data'

const route = useRoute()
const event = getEvent(String(route.params.id))
if (!event) throw createError({ statusCode: 404, statusMessage: '事件不存在', fatal: true })

const confidenceClass: Record<string, string> = {
  高: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  中: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  低: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-300',
}

useSeoMeta({ title: event.title })
</script>

<template>
  <div v-if="event" class="space-y-4">
    <div class="flex items-center justify-between">
      <BackButton fallback="/" />
      <span class="mono text-xs text-slate-400 dark:text-zinc-500">事件 · {{ event.id }}</span>
    </div>
    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center gap-2">
        <IdentityBadge :identity="event.identity" />
        <span class="chip bg-slate-200/70 text-slate-600 dark:bg-zinc-800 dark:text-zinc-300">{{ event.category }}</span>
        <span v-if="event.important" class="chip bg-rose-500/10 font-medium text-rose-600 dark:text-rose-400">重要</span>
        <span v-if="event.corrections?.length" class="chip border border-amber-500/40 text-amber-700 dark:text-amber-300">已更正</span>
      </div>
      <h1 class="mt-2.5 text-lg font-semibold leading-snug text-slate-900 dark:text-zinc-50">{{ event.title }}</h1>
      <p class="mono mt-2 text-xs text-slate-400 dark:text-zinc-500">
        首次发现 {{ event.firstSeenAt }} · 最近更新 {{ event.updatedAt }} · {{ event.evidenceCount }} 条证据
      </p>
      <div class="mt-2.5">
        <RegionSyncStrip :regions="event.regions" :note="event.regionNote" />
      </div>
    </section>

    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">当前结论</h2>
        <span class="chip font-medium" :class="confidenceClass[event.confidence]">置信度 · {{ event.confidence }}</span>
      </div>
      <p class="mt-2 text-sm leading-relaxed text-slate-700 dark:text-zinc-200">{{ event.conclusion }}</p>
      <p v-if="event.statusChange" class="mono mt-2 text-xs text-blue-600 dark:text-blue-400">状态变化 · {{ event.statusChange }}</p>
      <p class="mt-3 border-t border-slate-100 pt-2.5 text-xs leading-relaxed text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        置信度理由：{{ event.confidenceReason }}
      </p>
    </section>

    <section class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">证据链（{{ event.sources.length }} 个来源）</h2>
      <div class="mt-3">
        <SourceTable :sources="event.sources" />
      </div>
    </section>

    <section v-if="event.conflicts" class="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4">
      <h2 class="text-sm font-semibold text-amber-800 dark:text-amber-200">冲突证据</h2>
      <p class="mt-1.5 text-sm leading-relaxed text-amber-800/90 dark:text-amber-200/90">{{ event.conflicts }}</p>
    </section>

    <section v-if="event.corrections?.length" class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">修正历史</h2>
      <ul class="mt-3 space-y-3 border-l border-slate-200 pl-4 dark:border-zinc-800">
        <li v-for="c in event.corrections" :key="c.at" class="relative text-sm leading-relaxed">
          <span class="absolute -left-[19px] top-2 size-1.5 rounded-full bg-amber-500" />
          <span class="mono text-xs text-slate-400 dark:text-zinc-500">{{ c.at }}</span>
          <p class="mt-0.5 text-slate-600 dark:text-zinc-300">{{ c.note }}</p>
        </li>
      </ul>
      <p class="mt-3 text-xs text-slate-400 dark:text-zinc-500">事件结论可以修正；原始数据不可覆盖，AI 结果可以重算。</p>
    </section>

    <section class="panel p-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-slate-600 dark:text-zinc-300">此类状态变化，订阅后由邮件附带证据即时送达。</p>
        <NuxtLink to="/watch" class="btn-primary px-3 py-1.5"> 配置监控位 </NuxtLink>
      </div>
    </section>
  </div>
</template>
