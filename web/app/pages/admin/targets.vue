<script setup lang="ts">
import type { CollectTarget } from '~/utils/admin-types'
import { fmtTime, platformLabels, targetTypeLabels } from '~/utils/admin-types'
import { ADMIN_COMMUNITIES } from '~/utils/admin-communities'

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
  min_views: 10000,
  tier: 'general',
  community: 'wuwa',
})
const creating = ref(false)
const formError = ref<string | null>(null)

const communityLabels: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.label]),
)
// 分区筛选：全部 | 鸣潮 | 碧蓝航线
const communityFilter = ref<'' | 'wuwa' | 'blhx'>('')
const filteredTargets = computed(() =>
  communityFilter.value ? targets.value.filter(t => t.community === communityFilter.value) : targets.value,
)

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
        tier: form.tier,
        community: form.community,
      },
    })
    form.target_value = ''
    form.display_name = ''
    form.co_terms = ''
    form.min_views = 10000
    form.tier = 'general'
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

async function toggleTier(target: CollectTarget) {
  if (busyId.value) return
  busyId.value = target.id
  try {
    const next = target.tier === 'core' ? 'general' : 'core'
    const data = await api<CollectTarget>(`/api/admin/collect-targets/${target.id}`, {
      method: 'PUT',
      body: { tier: next },
    })
    target.tier = data.tier
    toast.ok(`${target.display_name || target.target_value} 已切换为${next === 'core' ? '核心关键词' : '泛关键词'}`)
  } catch (exc: any) {
    toast.err(`切换失败：${exc?.message ?? exc}`)
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
    await refreshRuns()
  } finally {
    busyId.value = null
  }
}

// 最近采集运行：分级/门槛策略的过滤效果在这里可见
interface RunItem {
  id: number
  target_id: number
  started_at: string | null
  status: string
  items_found: number | null
  items_new: number | null
  items_fetched: number | null
  items_filtered: number | null
}
const runs = ref<RunItem[]>([])
const targetName = (targetId: number) =>
  targets.value.find((t) => t.id === targetId)?.display_name || `#${targetId}`

async function refreshRuns() {
  try {
    runs.value = await api<RunItem[]>('/api/admin/runs', { query: { limit: 8 } })
  } catch { /* 运行记录失败不影响主表 */ }
}
onMounted(refreshRuns)
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
      <select v-model="form.platform" class="select px-3 py-2 text-sm">
        <option v-for="(label, value) in platformLabels" :key="value" :value="value">{{ label }}</option>
      </select>
      <select v-model="form.target_type" class="select px-3 py-2 text-sm">
        <option v-for="type in availableTypes" :key="type" :value="type">{{ targetTypeLabels[type] ?? type }}</option>
      </select>
      <input
        v-model="form.target_value"
        placeholder="关键词 / 账号 UID / 频道 ID（公告类可留占位）"
        class="input px-3 py-2 text-sm"
      >
      <input v-model.number="form.interval_minutes" type="number" min="5" max="1440" class="input mono px-3 py-2 text-sm">
      <select v-model="form.tier" class="select px-3 py-2 text-sm sm:col-span-2" title="核心关键词：商品/联名等特定内容，全量入库强及时；泛关键词：社区泛话题，走共现词与热度门槛">
        <option value="core">准入分级：核心关键词（全量入库，强及时）</option>
        <option value="general">准入分级：泛关键词（走共现词与热度门槛）</option>
      </select>
      <select v-model="form.community" class="select px-3 py-2 text-sm" title="所属游戏分区：决定内容与事件的归属">
        <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">分区：{{ c.label }}</option>
      </select>
      <input v-model="form.co_terms" placeholder="共现词（逗号分隔，如 键盘,开箱,发货；留空不过滤；核心分级不生效）" class="input px-3 py-2 text-sm sm:col-span-2">
      <input v-model.number="form.min_views" type="number" min="0" :placeholder="form.tier === 'core' ? '最低播放量（核心分级可选，0 不过滤）' : '最低播放量（默认 10000；0 不过滤）'" class="input mono px-3 py-2 text-sm">
      <button type="submit" class="btn-primary px-4 py-2" :disabled="creating || !form.target_value.trim()">
        {{ creating ? '创建中…' : '新增目标' }}
      </button>
      <p v-if="formError" class="col-span-full text-sm text-red-600 dark:text-red-400">{{ formError }}</p>
    </form>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-sm font-semibold">目标列表</h2>
      <div class="flex items-center gap-1">
        <button
          v-for="option in [['', '全部分区'], ...ADMIN_COMMUNITIES.map(c => [c.key, c.label] as const)] as const"
          :key="option[0]"
          class="rounded-full px-2.5 py-1 text-xs transition-colors"
          :class="communityFilter === option[0] ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
          @click="communityFilter = option[0]"
        >
          {{ option[1] }}
        </button>
      </div>
    </div>

    <div class="panel overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-3 py-2.5 font-medium">目标</th>
            <th class="px-2.5 py-2.5 font-medium">平台 · 类型</th>
            <th class="px-2.5 py-2.5 font-medium">分区</th>
            <th class="px-2.5 py-2.5 font-medium">分级</th>
            <th class="px-2.5 py-2.5 font-medium">播放门槛</th>
            <th class="px-2.5 py-2.5 font-medium">频率</th>
            <th class="px-3 py-2.5 font-medium">最近错误</th>
            <th class="px-3 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="target in filteredTargets" :key="target.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5">
            <td class="px-3 py-2.5">
              <span class="font-medium">{{ target.display_name && target.display_name !== target.target_value ? target.display_name : target.target_value }}</span>
              <span v-if="target.display_name && target.display_name !== target.target_value" class="mono ml-1.5 text-xs text-slate-400">{{ target.target_value }}</span>
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5 text-slate-600 dark:text-zinc-300">
              {{ platformLabels[target.platform] ?? target.platform }}
              <span class="text-slate-400">·</span>
              {{ targetTypeLabels[target.target_type] ?? target.target_type }}
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5">
              <span
                class="chip whitespace-nowrap"
                :class="target.community === 'blhx' ? 'bg-cyan-500/10 text-cyan-700 dark:bg-cyan-400/15 dark:text-cyan-300' : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
              >
                {{ communityLabels[target.community] ?? target.community }}
              </span>
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5">
              <button
                class="chip cursor-pointer whitespace-nowrap transition-colors"
                :class="target.tier === 'core' ? 'bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300' : 'bg-slate-500/10 text-slate-500 dark:text-zinc-400'"
                :title="target.tier === 'core' ? '核心关键词：全量入库，强及时。点击切换为泛关键词' : '泛关键词：走共现词与热度门槛。点击切换为核心关键词'"
                :disabled="busyId === target.id"
                @click="toggleTier(target)"
              >
                {{ target.tier === 'core' ? '核心' : '泛关键词' }}
              </button>
            </td>
            <td class="mono whitespace-nowrap px-2.5 py-2.5 text-xs text-slate-500 dark:text-zinc-400">{{ target.min_views ? `≥${target.min_views}` : '—' }}</td>
            <td class="mono whitespace-nowrap px-2.5 py-2.5 text-slate-600 dark:text-zinc-300">{{ target.interval_minutes }}min</td>
            <td class="max-w-40 truncate px-3 py-2.5 text-xs text-slate-400" :title="target.last_error ?? ''">
              {{ target.last_error ?? '—' }}
            </td>
            <td class="whitespace-nowrap px-3 py-2.5 text-right">
              <button class="rounded px-1.5 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === target.id" @click="runNow(target)">立即采集</button>
              <button class="rounded px-1.5 py-1 text-xs text-slate-500 hover:underline dark:text-zinc-400" :disabled="busyId === target.id" @click="toggle(target)">
                {{ target.enabled ? '停用' : '启用' }}
              </button>
              <button class="rounded px-1.5 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busyId === target.id" @click="remove(target)">删除</button>
            </td>
          </tr>
          <tr v-if="targets.length === 0">
            <td colspan="8" class="px-4 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">暂无目标</td>
          </tr>
        </tbody>
      </table>
    </div>

    <section class="panel overflow-hidden">
      <div class="flex items-center justify-between px-4 py-3">
        <h2 class="text-sm font-semibold">最近采集运行</h2>
        <p class="text-xs text-slate-400">抓取 → 过滤（分级/门槛） → 入库</p>
      </div>
      <table class="w-full text-sm">
        <tbody>
          <tr v-for="run in runs" :key="run.id" class="border-t border-slate-900/5 text-xs dark:border-white/5">
            <td class="px-4 py-2 text-slate-600 dark:text-zinc-300">{{ targetName(run.target_id) }}</td>
            <td class="mono px-4 py-2 text-slate-400">{{ fmtTime(run.started_at) }}</td>
            <td class="px-4 py-2">
              <span class="chip" :class="run.status === 'success' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' : 'bg-slate-500/10 text-slate-500'">{{ run.status }}</span>
            </td>
            <td class="mono px-4 py-2 text-slate-500 dark:text-zinc-400">
              抓取 {{ run.items_fetched ?? run.items_found ?? '—' }} · 过滤 {{ run.items_filtered ?? 0 }} · 入库 {{ run.items_new ?? 0 }}
            </td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="4" class="px-4 py-6 text-center text-sm text-slate-400">暂无运行记录。</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
