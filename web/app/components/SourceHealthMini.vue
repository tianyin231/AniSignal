<script setup lang="ts">
import type { PublicSource } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'

/**
 * 轻量来源状态卡：支持外部直接传入 sources（主题流免重复请求），
 * 也支持无参挂载自动拉取当前分区来源（供 RightRail 使用）。
 */
interface SimpleSource {
  name: string
  platform?: string
  enabled?: boolean
  last_success_at: string | null
  consecutive_failures: number
}

const props = withDefaults(defineProps<{
  sources?: SimpleSource[]
  title?: string
  showMoreLink?: boolean
  limit?: number
}>(), {
  sources: undefined,
  title: '来源健康',
  showMoreLink: true,
  limit: 6,
})

const { api } = usePublicApi()
const { community } = useGameSection()
const shouldFetch = computed(() => props.sources === undefined)
const { data } = await useLazyAsyncData(
  () => `rail-sources-${community.value}`,
  (_app, { signal }) => api<{ items: PublicSource[] }>('/api/sources', { query: { community: community.value }, signal }),
  { enabled: shouldFetch, watch: [community] },
)

const shown = computed(() => {
  const list = props.sources ?? (data.value?.items ?? []).filter(s => s.enabled)
  return list.slice(0, props.limit).map(s => ({
    ...s,
    ok: s.consecutive_failures === 0,
  }))
})
</script>

<template>
  <section v-if="shown.length" class="panel p-4 sm:p-5">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-bold text-ink">{{ title }}</h2>
      <NuxtLink v-if="showMoreLink" to="/sources" class="link text-xs">全部来源</NuxtLink>
      <span v-else class="num text-xs text-faint">{{ shown.length }} 源</span>
    </div>
    <ul class="mt-3 space-y-2">
      <li v-for="s in shown" :key="`${s.platform || ''}-${s.name}`" class="flex items-center justify-between gap-2 text-xs">
        <span class="flex min-w-0 items-center gap-2">
          <span class="size-1.5 shrink-0 rounded-full" :class="s.ok ? 'bg-ok' : 'bg-warn'" aria-hidden="true" />
          <span class="truncate text-ink-2">{{ s.name }}</span>
        </span>
        <span class="mono shrink-0 text-[11px] text-faint">{{ s.last_success_at ? fmtTime(s.last_success_at).slice(5, 16) : '待同步' }}</span>
      </li>
    </ul>
  </section>
</template>
