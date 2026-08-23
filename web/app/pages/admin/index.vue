<script setup lang="ts">
import type { Overview } from '~/utils/admin-types'
import { fmtTime, platformLabels, targetTypeLabels } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '总览 · AniSignal 管理看板' })

const { api } = useAdminApi()
const data = ref<Overview | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    data.value = await api<Overview>('/admin/overview')
  } catch (exc: any) {
    error.value = exc?.statusCode === 401 ? 'Token 失效，请重新登录' : `加载失败：${exc?.message ?? exc}`
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(refresh, 30_000) })
onUnmounted(() => timer && clearInterval(timer))

const stats = computed(() => {
  const targets = data.value?.targets ?? []
  return [
    { label: '采集目标', value: targets.length },
    { label: '启用中', value: targets.filter((t) => t.enabled).length },
    { label: '失败告警', value: targets.filter((t) => t.consecutive_failures > 0).length, warn: true },
    { label: '已入库内容', value: data.value?.total_contents ?? 0 },
    { label: '队列任务', value: data.value?.queue.pending_jobs ?? '—' },
  ]
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-lg font-semibold">总览</h1>
        <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
          来源健康与队列状态；"没抓到"必须可见，不代表没有消息。
        </p>
      </div>
      <button
        class="rounded-md border border-slate-900/10 px-3 py-1.5 text-sm transition-colors hover:bg-slate-900/5 dark:border-white/15 dark:hover:bg-white/10"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="grid grid-cols-2 gap-3 sm:grid-cols-5">
      <div v-for="stat in stats" :key="stat.label" class="panel px-4 py-3">
        <p class="text-xs text-slate-500 dark:text-zinc-400">{{ stat.label }}</p>
        <p class="mono mt-1 text-xl font-semibold" :class="stat.warn && stat.value > 0 ? 'text-red-600 dark:text-red-400' : ''">
          {{ stat.value }}
        </p>
      </div>
    </div>

    <div class="panel overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">目标</th>
            <th class="px-4 py-2.5 font-medium">平台</th>
            <th class="px-4 py-2.5 font-medium">频率</th>
            <th class="px-4 py-2.5 font-medium">下次运行</th>
            <th class="px-4 py-2.5 font-medium">最近成功</th>
            <th class="px-4 py-2.5 font-medium">状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="target in data?.targets ?? []"
            :key="target.id"
            class="border-b border-slate-900/5 last:border-0 dark:border-white/5"
          >
            <td class="max-w-56 truncate px-4 py-2.5">
              <span class="font-medium">{{ target.display_name || target.target_value }}</span>
              <span class="mono ml-2 text-xs text-slate-400">{{ targetTypeLabels[target.target_type] ?? target.target_type }}</span>
            </td>
            <td class="px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ platformLabels[target.platform] ?? target.platform }}</td>
            <td class="mono px-4 py-2.5">{{ target.interval_minutes }}min</td>
            <td class="mono px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ fmtTime(target.next_run_at) }}</td>
            <td class="mono px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ fmtTime(target.last_success_at) }}</td>
            <td class="px-4 py-2.5">
              <span v-if="!target.enabled" class="chip bg-slate-500/10 text-slate-500 dark:text-zinc-400">已停用</span>
              <span v-else-if="target.consecutive_failures > 0" class="chip bg-red-500/10 text-red-600 dark:text-red-400" :title="target.last_error ?? ''">
                连续失败 ×{{ target.consecutive_failures }}
              </span>
              <span v-else class="chip bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">正常</span>
            </td>
          </tr>
          <tr v-if="(data?.targets ?? []).length === 0">
            <td colspan="6" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">
              还没有采集目标，去「采集目标」页创建一个。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
