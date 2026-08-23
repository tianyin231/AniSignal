<script setup lang="ts">
import { platformMeta, regionMeta, type SourceRef } from '~/utils/data'

defineProps<{ sources: SourceRef[] }>()
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[600px] text-left text-[13px]">
      <thead>
        <tr class="border-b border-slate-200 text-xs text-slate-400 dark:border-zinc-800">
          <th class="py-2 pr-3 font-medium">平台</th>
          <th class="py-2 pr-3 font-medium">账号</th>
          <th class="py-2 pr-3 font-medium">区域</th>
          <th class="py-2 pr-3 font-medium">发布时间</th>
          <th class="py-2 pr-3 font-medium">译文</th>
          <th class="py-2 font-medium">原文</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100 dark:divide-zinc-800/60">
        <tr v-for="s in sources" :key="s.platform + s.account + s.publishedAt">
          <td class="py-2 pr-3">
            <span class="inline-flex items-center gap-1.5">
              <span
                class="grid size-4 shrink-0 place-items-center rounded-full text-[8px] font-bold text-white"
                :class="platformMeta[s.platform].badge"
                >{{ platformMeta[s.platform].short }}</span>
              <span class="text-slate-700 dark:text-zinc-300">{{ platformMeta[s.platform].label }}</span>
            </span>
          </td>
          <td class="py-2 pr-3 text-slate-600 dark:text-zinc-400">{{ s.account }}</td>
          <td class="py-2 pr-3 text-slate-600 dark:text-zinc-400">{{ regionMeta[s.region] }}</td>
          <td class="mono py-2 pr-3 text-slate-500 dark:text-zinc-400">{{ s.publishedAt }}</td>
          <td class="py-2 pr-3">
            <span v-if="s.translated" class="chip bg-sky-500/10 text-sky-700 dark:text-sky-300">已译</span>
            <span v-else class="text-slate-300 dark:text-zinc-600">—</span>
          </td>
          <td class="py-2">
            <a v-if="s.url" :href="s.url" target="_blank" rel="noopener" class="link">打开 ↗</a>
            <span v-else class="text-slate-300 dark:text-zinc-600">—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
