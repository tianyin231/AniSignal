<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
import { platformInfo, platformRegion, TARGET_TYPE_LABELS } from '~/utils/lexicon'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import type { PublicSource } from '~/composables/usePublicApi'

useSeoMeta({ title: '来源健康', description: '各采集来源的最近运行状态与健康情况。' })

const { api } = usePublicApi()
// 系统级页面：展示全部分区的来源，不跟随顶栏分区
const { data, status, error, refresh } = await useLazyAsyncData(
  'public-sources-all',
  (_app, { signal }) => api<{ items: PublicSource[] }>('/api/sources', { query: { community: 'all' }, signal }),
)

function communityBadge(community?: string) {
  const meta = communityMeta(community)
  return meta ? { label: meta.label, chip: communityClasses(meta.color).chip } : null
}
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-4 sm:p-5">
      <h1 class="text-2xl font-bold text-ink">来源健康</h1>
    </section>

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-muted">加载中…</section>
    <section v-else-if="error" role="alert" class="panel px-4 py-10 text-center text-sm text-warn">来源暂时加载失败。<button type="button" class="ml-1 underline" @click="refresh()">重新加载</button></section>
    <section v-else class="panel overflow-x-auto">
      <table class="w-full min-w-[720px] text-left text-[13px]">
        <thead>
          <tr class="border-b border-line text-xs text-muted">
            <th class="whitespace-nowrap px-4 py-2.5 font-medium">来源</th>
            <th class="whitespace-nowrap px-3 py-2.5 font-medium">分区</th>
            <th class="whitespace-nowrap px-3 py-2.5 font-medium">平台</th>
            <th class="whitespace-nowrap px-3 py-2.5 font-medium">最近成功采集</th>
            <th class="whitespace-nowrap px-3 py-2.5 font-medium">状态</th>
            <th class="whitespace-nowrap px-4 py-2.5 font-medium">说明</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line-soft">
          <tr
            v-for="s in data?.items ?? []"
            :key="`${s.platform}-${s.name}`"
            :class="s.consecutive_failures > 0 ? 'bg-amber-500/5' : ''"
          >
            <td class="px-4 py-2.5 text-ink">
              <span class="font-medium">{{ s.name }}</span>
              <span class="ml-1.5 whitespace-nowrap text-xs text-faint">{{ TARGET_TYPE_LABELS[s.target_type] ?? s.target_type }}</span>
            </td>
            <td class="whitespace-nowrap px-3 py-2.5">
              <span
                v-if="communityBadge(s.community)"
                class="chip whitespace-nowrap"
                :class="communityBadge(s.community)!.chip"
              >{{ communityBadge(s.community)!.label }}</span>
            </td>
            <td class="whitespace-nowrap px-3 py-2.5">
              <span class="inline-flex items-center gap-1.5 text-ink-2">
                <span
                  class="grid size-4 shrink-0 place-items-center rounded-full text-[8px] font-bold text-white"
                  :class="platformInfo(s.platform).badge"
                >{{ platformInfo(s.platform).short }}</span>
                <span>{{ platformInfo(s.platform).label }}</span>
                <span class="text-xs text-faint">· {{ platformRegion(s.platform) }}</span>
              </span>
            </td>
            <td class="mono whitespace-nowrap tabular-nums px-3 py-2.5 text-muted">
              {{ s.last_success_at ? fmtTime(s.last_success_at) : '尚未成功' }}
            </td>
            <td class="whitespace-nowrap px-3 py-2.5">
              <span v-if="!s.enabled" class="chip bg-zinc-500/10 font-medium text-zinc-500 dark:text-zinc-400">已停用</span>
              <span v-else-if="s.consecutive_failures > 0" class="chip bg-amber-500/15 font-medium text-amber-700 dark:text-amber-300">异常</span>
              <span v-else class="chip bg-emerald-500/10 font-medium text-emerald-700 dark:text-emerald-300">正常</span>
            </td>
            <td class="px-4 py-2.5 text-xs text-muted">
              {{ s.consecutive_failures > 0 ? `连续失败 ${s.consecutive_failures} 次；期间「未发现」不代表无新消息` : '—' }}
            </td>
          </tr>
          <tr v-if="!(data?.items ?? []).length">
            <td colspan="6" class="px-4 py-10 text-center text-sm text-faint">暂无已接入来源</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
