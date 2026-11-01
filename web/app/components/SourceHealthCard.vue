<script setup lang="ts">
import type { PublicSource } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { platformMeta, type Platform } from '~/utils/data'

// 来源健康：一条正常/失败比例条 + 分组计数 + 失败来源点名。
// 跨分区（聚合首页）按游戏分组，单分区按平台分组；只统计启用中的来源。
type HealthSource = Pick<PublicSource, 'name' | 'community' | 'platform' | 'enabled' | 'consecutive_failures'>
const props = defineProps<{ sources: HealthSource[] }>()

const enabled = computed(() => props.sources.filter(s => s.enabled))
const failing = computed(() =>
  enabled.value.filter(s => s.consecutive_failures > 0).sort((a, b) => b.consecutive_failures - a.consecutive_failures),
)
const okCount = computed(() => enabled.value.length - failing.value.length)
const okPercent = computed(() => (enabled.value.length ? (okCount.value / enabled.value.length) * 100 : 0))

const byCommunity = computed(() => new Set(enabled.value.map(s => s.community)).size > 1)
const groups = computed(() => {
  const map = new Map<string, { key: string, label: string, dot: string, total: number, failed: number }>()
  for (const s of enabled.value) {
    const key = (byCommunity.value ? s.community : s.platform) || 'other'
    let group = map.get(key)
    if (!group) {
      const community = byCommunity.value ? communityMeta(key) : undefined
      const platform = byCommunity.value ? undefined : platformMeta[key as Platform]
      group = {
        key,
        label: community ? (community.key === 'ai' ? 'AI 快讯' : community.label) : platform?.label ?? key,
        dot: community ? communityClasses(community.color).dot : platform?.badge ?? 'bg-faint',
        total: 0,
        failed: 0,
      }
      map.set(key, group)
    }
    group.total++
    if (s.consecutive_failures > 0) group.failed++
  }
  return [...map.values()].sort((a, b) => b.failed - a.failed || b.total - a.total)
})
const summary = computed(() =>
  failing.value.length
    ? `${enabled.value.length} 个来源中 ${okCount.value} 个正常，${failing.value.length} 个连续采集失败`
    : `${enabled.value.length} 个来源全部正常`,
)
</script>

<template>
  <section v-if="enabled.length" class="panel p-5" aria-labelledby="source-health-title">
    <header class="flex items-baseline justify-between">
      <h2 id="source-health-title" class="text-base font-bold text-ink">来源健康</h2>
      <span class="num text-[15px] font-bold text-ink">{{ okCount }}<span class="font-medium text-faint">/{{ enabled.length }}</span></span>
    </header>

    <div class="mt-3 flex h-1.5 overflow-hidden rounded-full bg-warn" role="img" :aria-label="summary">
      <span class="h-full rounded-full bg-ok/80" :style="{ width: `${okPercent}%` }" />
    </div>
    <p class="mt-2 text-xs" :class="failing.length ? 'text-warn' : 'text-muted'">
      {{ failing.length ? `${failing.length} 个来源连续采集失败` : '全部来源采集正常' }}
    </p>

    <ul class="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-line-soft pt-3 text-[13px]">
      <li v-for="g in groups" :key="g.key" class="flex min-w-0 items-center gap-1.5">
        <span class="size-[7px] shrink-0 rounded-full" :class="g.dot" aria-hidden="true" />
        <span class="min-w-0 flex-1 truncate text-ink-2">{{ g.label }}</span>
        <span class="num shrink-0 font-semibold" :class="g.failed ? 'text-warn' : 'text-ink-2'">
          {{ g.total - g.failed }}<span v-if="g.failed" class="font-medium text-faint">/{{ g.total }}</span>
        </span>
      </li>
    </ul>

    <ul v-if="failing.length" class="mt-3 space-y-1 border-t border-line-soft pt-3 text-xs">
      <li v-for="s in failing.slice(0, 3)" :key="`${s.community}-${s.name}`" class="flex items-center justify-between gap-2">
        <span class="flex min-w-0 items-center gap-1.5">
          <span class="size-1.5 shrink-0 rounded-full bg-warn" aria-hidden="true" />
          <span class="truncate text-ink-2" :title="s.name">{{ s.name }}</span>
        </span>
        <span class="shrink-0 text-faint">连续失败 {{ s.consecutive_failures }} 次</span>
      </li>
      <li v-if="failing.length > 3" class="text-faint">另有 {{ failing.length - 3 }} 个…</li>
    </ul>

    <NuxtLink to="/sources" class="link mt-3 inline-block text-[13px]">全部来源</NuxtLink>
  </section>
</template>
