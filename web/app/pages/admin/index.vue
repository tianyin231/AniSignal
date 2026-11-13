<script setup lang="ts">
import type { Overview } from '~/utils/admin-types'
import { fmtTime, platformLabels, targetTypeLabels } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '总览 · AniSignal 管理看板' })

const { api } = useAdminApi()
const { aiConfigured, smtpConfigured } = useSiteConfig()
const data = ref<Overview | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
const showOnboarding = computed(() => Boolean(data.value && ((data.value.targets?.length ?? 0) === 0 || !aiConfigured.value)))

async function refresh() {
  loading.value = true
  error.value = null
  try {
    data.value = await api<Overview>('/api/admin/overview')
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
    <div class="wall-heading flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">总览</h1>
        <p class="mt-0.5 text-sm text-muted">
          来源健康与队列状态；"没抓到"必须可见，不代表没有消息。
        </p>
      </div>
      <button
        class="rounded-md border border-line px-3 py-1.5 text-sm transition-colors hover:bg-chip"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <section v-if="showOnboarding" class="panel border-blue-500/30 bg-blue-500/5 p-5">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h2 class="text-sm font-semibold text-ink">新站点初始化引导</h2>
        <span class="text-xs text-muted">无需登录服务器，全部在网页后台完成</span>
      </div>
      <div class="mt-3 grid gap-3 sm:grid-cols-3">
        <NuxtLink to="/admin/targets" class="rounded-xl border border-line bg-card p-3.5 transition-colors hover:border-blue-500/50">
          <p class="text-xs font-semibold text-blue-600 dark:text-blue-300">1. 分区与信源定制 · {{ (data?.targets.length ?? 0) > 0 ? '已接入' : '待导入' }}</p>
          <p class="mt-1 text-xs leading-relaxed text-muted">新建或隐藏任意分区，一键按分区导入预设信源，或批量移除不需要的监控源。</p>
        </NuxtLink>
        <NuxtLink to="/admin/ai" class="rounded-xl border border-line bg-card p-3.5 transition-colors hover:border-blue-500/50">
          <p class="text-xs font-semibold text-blue-600 dark:text-blue-300">2. AI 模型（可选） · {{ aiConfigured ? '已配置' : '当前原文直出' }}</p>
          <p class="mt-1 text-xs leading-relaxed text-muted">配置 OpenAI 兼容接口开启多源聚合与摘要；未配时前台自动直接展示原文。</p>
        </NuxtLink>
        <NuxtLink :to="smtpConfigured ? '/admin/targets' : '/admin/users'" class="rounded-xl border border-line bg-card p-3.5 transition-colors hover:border-blue-500/50">
          <p class="text-xs font-semibold text-blue-600 dark:text-blue-300">3. B 站登录与发信 · {{ smtpConfigured ? 'SMTP 已配' : '无 SMTP 托管模式' }}</p>
          <p class="mt-1 text-xs leading-relaxed text-muted">B 站搜索可在「采集目标」扫码登录；未配 SMTP 时可在「用户管理」查看注册码与重置密码。</p>
        </NuxtLink>
      </div>
    </section>

    <div class="grid grid-cols-2 gap-3 sm:grid-cols-5">
      <div v-for="stat in stats" :key="stat.label" class="panel px-4 py-3">
        <p class="text-xs text-muted">{{ stat.label }}</p>
        <p class="mono mt-1 text-xl font-semibold" :class="stat.warn && stat.value > 0 ? 'text-red-600 dark:text-red-400' : ''">
          {{ stat.value }}
        </p>
      </div>
    </div>

    <div class="panel overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-line-soft text-left text-xs text-muted dark:border-line">
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
            class="border-b border-line-soft last:border-0"
          >
            <td class="max-w-56 truncate px-4 py-2.5">
              <span class="font-medium">{{ target.display_name || target.target_value }}</span>
              <span class="mono ml-2 text-xs text-faint">{{ targetTypeLabels[target.target_type] ?? target.target_type }}</span>
            </td>
            <td class="px-4 py-2.5 text-ink-2">{{ platformLabels[target.platform] ?? target.platform }}</td>
            <td class="mono px-4 py-2.5">{{ target.interval_minutes }}min</td>
            <td class="mono px-4 py-2.5 text-ink-2">{{ fmtTime(target.next_run_at) }}</td>
            <td class="mono px-4 py-2.5 text-ink-2">{{ fmtTime(target.last_success_at) }}</td>
            <td class="px-4 py-2.5">
              <span v-if="!target.enabled" class="chip bg-chip text-muted">已停用</span>
              <span v-else-if="target.consecutive_failures > 0" class="chip bg-red-500/10 text-red-600 dark:text-red-400" :title="target.last_error ?? ''">
                连续失败 ×{{ target.consecutive_failures }}
              </span>
              <span v-else class="chip bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">正常</span>
            </td>
          </tr>
          <tr v-if="(data?.targets ?? []).length === 0">
            <td colspan="6" class="px-4 py-10 text-center text-sm text-faint">
              还没有采集目标，去「采集目标」页创建一个。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
