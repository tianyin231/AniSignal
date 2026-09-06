<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
import { platformMeta, type Platform, type Region } from '~/utils/data'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'
import type { PublicSource } from '~/composables/usePublicApi'

useSeoMeta({ title: '来源健康' })

const { api } = usePublicApi()
// 系统级页面：展示全部分区的来源，不跟随顶栏分区
const { data } = await useAsyncData(
  'public-sources-all',
  () => api<{ items: PublicSource[] }>('/api/sources', { query: { community: 'all' } }),
)

function communityBadge(community?: string) {
  const meta = GAME_COMMUNITIES.find(c => c.key === community)
  return meta ? { label: meta.label, chip: communityClasses(meta.color).chip } : null
}

const regionOf: Record<string, Region> = {
  bilibili: 'cn',
  miyoushe: 'cn',
  kuro_cn: 'cn',
  kuro_global: 'global',
  youtube: 'global',
}

const targetTypeLabels: Record<string, string> = {
  keyword_search: '关键词搜索',
  account_videos: '账号视频',
  account_dynamics: '账号动态',
  notices: '官方公告',
  channel_rss: '频道 RSS',
}
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-4 sm:p-5">
      <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">来源健康</h1>
    </section>

    <section class="panel overflow-x-auto">
      <table class="w-full min-w-[760px] text-left text-[13px]">
        <thead>
          <tr class="border-b border-slate-200 text-xs text-slate-400 dark:border-zinc-800">
            <th class="px-4 py-2.5 font-medium">来源</th>
            <th class="px-3 py-2.5 font-medium">分区</th>
            <th class="px-3 py-2.5 font-medium">平台</th>
            <th class="px-3 py-2.5 font-medium">区域</th>
            <th class="px-3 py-2.5 font-medium">最近成功采集</th>
            <th class="px-3 py-2.5 font-medium">状态</th>
            <th class="px-4 py-2.5 font-medium">说明</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 dark:divide-zinc-800/60">
          <tr
            v-for="s in data?.items ?? []"
            :key="`${s.platform}-${s.name}`"
            :class="s.consecutive_failures > 0 ? 'bg-amber-500/5' : ''"
          >
            <td class="px-4 py-2.5 text-slate-700 dark:text-zinc-200">
              {{ s.name }}
              <span class="ml-1.5 text-xs text-slate-400">{{ targetTypeLabels[s.target_type] ?? s.target_type }}</span>
            </td>
            <td class="px-3 py-2.5">
              <span
                v-if="communityBadge(s.community)"
                class="chip whitespace-nowrap"
                :class="communityBadge(s.community)!.chip"
              >{{ communityBadge(s.community)!.label }}</span>
            </td>
            <td class="px-3 py-2.5">
              <span class="inline-flex items-center gap-1.5 text-slate-600 dark:text-zinc-400">
                <span
                  class="grid size-4 shrink-0 place-items-center rounded-full text-[8px] font-bold text-white"
                  :class="platformMeta[s.platform as Platform].badge"
                >{{ platformMeta[s.platform as Platform].short }}</span>
                {{ platformMeta[s.platform as Platform].label }}
              </span>
            </td>
            <td class="px-3 py-2.5 text-slate-600 dark:text-zinc-400">{{ regionOf[s.platform] === 'global' ? '国际服' : '国服' }}</td>
            <td class="mono px-3 py-2.5 text-slate-500 dark:text-zinc-400">
              {{ s.last_success_at ? fmtTime(s.last_success_at) : '尚未成功' }}
            </td>
            <td class="px-3 py-2.5">
              <span v-if="!s.enabled" class="chip bg-zinc-500/10 font-medium text-zinc-500 dark:text-zinc-400">已停用</span>
              <span v-else-if="s.consecutive_failures > 0" class="chip bg-amber-500/15 font-medium text-amber-700 dark:text-amber-300">不可用</span>
              <span v-else class="chip bg-emerald-500/10 font-medium text-emerald-700 dark:text-emerald-300">正常</span>
            </td>
            <td class="px-4 py-2.5 text-xs text-slate-400 dark:text-zinc-500">
              {{ s.consecutive_failures > 0 ? `连续失败 ${s.consecutive_failures} 次；该来源范围内的「未发现」不代表无消息` : '—' }}
            </td>
          </tr>
          <tr v-if="!(data?.items ?? []).length">
            <td colspan="7" class="px-4 py-10 text-center text-sm text-slate-400">暂无已接入来源。</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
