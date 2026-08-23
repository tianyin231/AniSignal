<script setup lang="ts">
import type { ContentList, ContentItem } from '~/utils/admin-types'
import { contentTypeLabels, fmtTime, platformLabels } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '内容浏览 · AniSignal 管理看板' })

const { api } = useAdminApi()
const items = ref<ContentItem[]>([])
const total = ref(0)
const platform = ref<string>('')
const page = ref(1)
const pageSize = 50
const error = ref<string | null>(null)
const loading = ref(false)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const query: Record<string, string | number> = { limit: pageSize, offset: (page.value - 1) * pageSize }
    if (platform.value) query.platform = platform.value
    const data = await api<ContentList>('/admin/contents', { query })
    items.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
watch([platform, page], refresh)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const metricText = (metrics: Record<string, number> | null) => {
  if (!metrics) return '—'
  const parts: string[] = []
  if (metrics.view !== undefined) parts.push(`播 ${metrics.view}`)
  if (metrics.like !== undefined) parts.push(`赞 ${metrics.like}`)
  if (metrics.reply !== undefined) parts.push(`评 ${metrics.reply}`)
  return parts.join(' · ') || '—'
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold">内容浏览</h1>
        <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
          最近采集入库的内容与最新指标快照（时间均为北京时间）。
        </p>
      </div>
      <select
        v-model="platform"
        class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
      >
        <option value="">全部平台</option>
        <option v-for="(label, value) in platformLabels" :key="value" :value="value">{{ label }}</option>
      </select>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-3xl text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">标题</th>
            <th class="px-4 py-2.5 font-medium">平台</th>
            <th class="px-4 py-2.5 font-medium">类型</th>
            <th class="px-4 py-2.5 font-medium">作者</th>
            <th class="px-4 py-2.5 font-medium">发布</th>
            <th class="px-4 py-2.5 font-medium">采集</th>
            <th class="px-4 py-2.5 font-medium">最新指标</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="max-w-80 px-4 py-2.5">
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="link block truncate font-medium">{{ item.title || item.external_id }}</a>
              <span v-else class="block truncate">{{ item.title || item.external_id }}</span>
              <span class="mono text-xs text-slate-400">v{{ item.version ?? '?' }} · {{ item.external_id }}</span>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ platformLabels[item.platform] ?? item.platform }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ contentTypeLabels[item.content_type] ?? item.content_type }}</td>
            <td class="max-w-32 truncate px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ item.source_name ?? '—' }}</td>
            <td class="mono whitespace-nowrap px-4 py-2.5 text-slate-500 dark:text-zinc-400">{{ fmtTime(item.published_at) }}</td>
            <td class="mono whitespace-nowrap px-4 py-2.5 text-slate-500 dark:text-zinc-400">{{ fmtTime(item.collected_at) }}</td>
            <td class="mono whitespace-nowrap px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ metricText(item.latest_metrics) }}</td>
          </tr>
          <tr v-if="items.length === 0 && !loading">
            <td colspan="7" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">
              暂无内容；创建目标并触发采集后，这里会出现数据。
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="totalPages > 1" class="flex items-center justify-center gap-4 text-sm">
      <button
        class="rounded-md border border-slate-900/10 px-3 py-1.5 disabled:opacity-40 dark:border-white/15"
        :disabled="page <= 1"
        @click="page--"
      >
        上一页
      </button>
      <span class="mono">{{ page }} / {{ totalPages }}</span>
      <button
        class="rounded-md border border-slate-900/10 px-3 py-1.5 disabled:opacity-40 dark:border-white/15"
        :disabled="page >= totalPages"
        @click="page++"
      >
        下一页
      </button>
    </div>
  </div>
</template>
