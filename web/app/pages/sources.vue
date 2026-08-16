<script setup lang="ts">
import { sourcesHealth, platformMeta, regionMeta } from '~/utils/data'

useSeoMeta({ title: '来源健康' })
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-4 sm:p-5">
      <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">来源健康</h1>
      <p class="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        本页公开展示已接入来源、最近成功采集时间与不可用状态。全站任何「未发现」均指「在已接入来源中未发现」，
        不宣传全网覆盖。必须登录或持续触发风控的来源不纳入可靠性承诺。
      </p>
    </section>

    <section class="panel overflow-x-auto">
      <table class="w-full min-w-[680px] text-left text-[13px]">
        <thead>
          <tr class="border-b border-slate-200 text-xs text-slate-400 dark:border-zinc-800">
            <th class="px-4 py-2.5 font-medium">来源</th>
            <th class="px-3 py-2.5 font-medium">平台</th>
            <th class="px-3 py-2.5 font-medium">区域</th>
            <th class="px-3 py-2.5 font-medium">最近成功采集</th>
            <th class="px-3 py-2.5 font-medium">状态</th>
            <th class="px-4 py-2.5 font-medium">说明</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 dark:divide-zinc-800/60">
          <tr v-for="s in sourcesHealth" :key="s.name" :class="s.status === 'fail' ? 'bg-amber-500/5' : ''">
            <td class="px-4 py-2.5 text-slate-700 dark:text-zinc-200">{{ s.name }}</td>
            <td class="px-3 py-2.5">
              <span class="inline-flex items-center gap-1.5 text-slate-600 dark:text-zinc-400">
                <span
                  class="grid size-4 shrink-0 place-items-center rounded-full text-[8px] font-bold text-white"
                  :class="platformMeta[s.platform].badge"
                  >{{ platformMeta[s.platform].short }}</span>
                {{ platformMeta[s.platform].label }}
              </span>
            </td>
            <td class="px-3 py-2.5 text-slate-600 dark:text-zinc-400">{{ regionMeta[s.region] }}</td>
            <td class="mono px-3 py-2.5 text-slate-500 dark:text-zinc-400">{{ s.lastOk }}</td>
            <td class="px-3 py-2.5">
              <span v-if="s.status === 'ok'" class="chip bg-emerald-500/10 font-medium text-emerald-700 dark:text-emerald-300">正常</span>
              <span v-else class="chip bg-amber-500/15 font-medium text-amber-700 dark:text-amber-300">不可用</span>
            </td>
            <td class="px-4 py-2.5 text-xs text-slate-400 dark:text-zinc-500">{{ s.note ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <p class="text-xs leading-relaxed text-slate-400 dark:text-zinc-500">
      「没有抓到」不会被解释为「没有消息」。来源失效必须显示为不可用或数据可能不完整；每个平台适配器可独立关闭，并支持人工补录关键事件。
    </p>
  </div>
</template>
