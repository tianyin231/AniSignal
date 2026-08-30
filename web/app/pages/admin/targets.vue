<script setup lang="ts">
import type { CollectTarget } from '~/utils/admin-types'
import { fmtTime, platformLabels, targetTypeLabels } from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '采集目标 · AniSignal 管理看板' })

const { api } = useAdminApi()
const targets = ref<CollectTarget[]>([])
const error = ref<string | null>(null)
const busyId = ref<number | null>(null)
const toast = useAdminToast()

// 与后端能力对齐的可选组合（platform → 支持的 target_type）
const combos: Record<string, string[]> = {
  bilibili: ['keyword_search', 'account_videos', 'account_dynamics'],
  miyoushe: ['keyword_search'],
  kuro_cn: ['notices'],
  kuro_global: ['notices'],
  youtube: ['channel_rss'],
}

const form = reactive({
  platform: 'bilibili',
  target_type: 'keyword_search',
  target_value: '',
  display_name: '',
  interval_minutes: 30,
  co_terms: '',
  min_views: 0,
})
const creating = ref(false)
const formError = ref<string | null>(null)

const availableTypes = computed(() => combos[form.platform] ?? [])
watch(() => form.platform, () => { form.target_type = availableTypes.value[0] ?? 'keyword_search' })

async function refresh() {
  try {
    targets.value = await api<CollectTarget[]>('/api/admin/collect-targets')
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}
onMounted(refresh)

async function create() {
  if (!form.target_value.trim() || creating.value) return
  creating.value = true
  formError.value = null
  try {
    await api('/api/admin/collect-targets', {
      method: 'POST',
      body: {
        platform: form.platform,
        target_type: form.target_type,
        target_value: form.target_value.trim(),
        display_name: form.display_name.trim() || null,
        interval_minutes: Number(form.interval_minutes) || 30,
        co_terms: form.co_terms.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean),
        min_views: Number(form.min_views) || 0,
      },
    })
    form.target_value = ''
    form.display_name = ''
    form.co_terms = ''
    form.min_views = 0
    await refresh()
  } catch (exc: any) {
    formError.value = exc?.statusCode === 409 ? '该目标已存在' : `创建失败：${exc?.data?.detail ?? exc?.message ?? exc}`
  } finally {
    creating.value = false
  }
}

async function toggle(target: CollectTarget) {
  busyId.value = target.id
  try {
    await api(`/api/admin/collect-targets/${target.id}`, {
      method: 'PUT',
      body: { enabled: !target.enabled },
    })
    toast.ok(target.enabled ? '已停用' : '已启用')
    await refresh()
  } finally {
    busyId.value = null
  }
}

async function runNow(target: CollectTarget) {
  busyId.value = target.id
  try {
    await api(`/api/admin/collect-targets/${target.id}/run`, { method: 'POST' })
    toast.info(`「${target.display_name || target.target_value}」已加入采集队列`)
  } catch (exc: any) {
    error.value = `触发失败：${exc?.message ?? exc}`
  } finally {
    busyId.value = null
  }
}

async function remove(target: CollectTarget) {
  if (!confirm(`删除目标「${target.display_name || target.target_value}」及其运行记录？`)) return
  busyId.value = target.id
  try {
    await api(`/api/admin/collect-targets/${target.id}`, { method: 'DELETE' })
    toast.ok('目标已删除')
    await refresh()
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-lg font-semibold">采集目标</h1>
      <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
        关键词、账号与频率配置；调度器按频率自动投递采集任务。
      </p>
    </div>

    <form class="panel grid gap-3 p-5 sm:grid-cols-[9rem_9rem_1fr_6rem_auto]" @submit.prevent="create">
      <select v-model="form.platform" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        <option v-for="(label, value) in platformLabels" :key="value" :value="value">{{ label }}</option>
      </select>
      <select v-model="form.target_type" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        <option v-for="type in availableTypes" :key="type" :value="type">{{ targetTypeLabels[type] ?? type }}</option>
      </select>
      <input
        v-model="form.target_value"
        placeholder="关键词 / 账号 UID / 频道 ID（公告类可留占位）"
        class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5"
      >
      <input v-model.number="form.interval_minutes" type="number" min="5" max="1440" class="mono rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
      <input v-model="form.co_terms" placeholder="共现词（逗号分隔，如 键盘,开箱,发货；留空不过滤）" class="rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm sm:col-span-2 dark:border-white/15 dark:bg-white/5">
      <input v-model.number="form.min_views" type="number" min="0" placeholder="最低播放量（0 不过滤）" class="mono rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
      <button type="submit" class="btn-primary px-4 py-2" :disabled="creating || !form.target_value.trim()">
        {{ creating ? '创建中…' : '新增目标' }}
      </button>
      <p v-if="formError" class="col-span-full text-sm text-red-600 dark:text-red-400">{{ formError }}</p>
    </form>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-3xl text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">目标</th>
            <th class="px-4 py-2.5 font-medium">平台</th>
            <th class="px-4 py-2.5 font-medium">分级</th>
            <th class="px-4 py-2.5 font-medium">类型</th>
            <th class="px-4 py-2.5 font-medium">频率</th>
            <th class="px-4 py-2.5 font-medium">最近错误</th>
            <th class="px-4 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="target in targets" :key="target.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="px-4 py-2.5">
              <span class="font-medium">{{ target.display_name || target.target_value }}</span>
              <span class="mono ml-2 text-xs text-slate-400">{{ target.target_value }}</span>
            </td>
            <td class="px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ platformLabels[target.platform] ?? target.platform }}</td>
            <td class="px-4 py-2.5">
              <span class="chip" :class="target.tier === 'core' ? 'bg-blue-500/10 text-blue-700 dark:text-blue-300' : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'" :title="target.tier === 'core' ? '核心关键词：全量入库，强及时' : '泛关键词：走共现词与热度门槛'">
                {{ target.tier === 'core' ? '核心' : '泛关键词' }}
              </span>
            </td>
            <td class="px-4 py-2.5 text-slate-600 dark:text-zinc-300">{{ targetTypeLabels[target.target_type] ?? target.target_type }}</td>
            <td class="mono px-4 py-2.5">{{ target.interval_minutes }}min</td>
            <td class="max-w-64 truncate px-4 py-2.5 text-xs text-slate-400" :title="target.last_error ?? ''">
              {{ target.last_error ?? '—' }}
            </td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <button class="rounded px-2 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === target.id" @click="runNow(target)">立即采集</button>
              <button class="rounded px-2 py-1 text-xs text-slate-500 hover:underline dark:text-zinc-400" :disabled="busyId === target.id" @click="toggle(target)">
                {{ target.enabled ? '停用' : '启用' }}
              </button>
              <button class="rounded px-2 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busyId === target.id" @click="remove(target)">删除</button>
            </td>
          </tr>
          <tr v-if="targets.length === 0">
            <td colspan="7" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">暂无目标</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
