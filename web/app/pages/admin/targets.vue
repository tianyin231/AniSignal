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
const biliSession = ref<{ state: string; message: string; checked_at?: string } | null>(null)
const checkingBili = ref(false)

async function loadBiliSession(check = false) {
  if (checkingBili.value) return
  checkingBili.value = true
  try {
    biliSession.value = await api('/api/admin/bilibili-session' + (check ? '/check' : ''), { method: check ? 'POST' : 'GET' })
    if (check) {
      const notify = biliSession.value?.state === 'logged_in' ? toast.ok : toast.info
      notify(biliSession.value?.message ?? '检测完成')
    }
  } catch (exc: any) {
    toast.err(exc?.message ?? 'B站登录状态检测失败')
  } finally {
    checkingBili.value = false
  }
}
onMounted(() => loadBiliSession())

// 与后端能力对齐的可选组合（platform → 支持的 target_type）
const combos: Record<string, string[]> = {
  bilibili: ['keyword_search', 'account_videos', 'account_dynamics'],
  miyoushe: ['keyword_search'],
  kuro_cn: ['notices'],
  kuro_global: ['notices'],
  youtube: ['channel_rss'],
  x: ['account_dynamics'],
  rss: ['feed'],
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
const editingId = ref<number | null>(null)

const communityLabels: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.label]),
)
// 分区筛选：全部 | 鸣潮 | 碧蓝航线
const communityFilter = ref('')
const filteredTargets = computed(() =>
  communityFilter.value ? targets.value.filter(t => t.community === communityFilter.value) : targets.value,
)

const availableTypes = computed(() => combos[form.platform] ?? [])
const supportsViews = computed(() => form.community !== 'ai' && form.platform === 'bilibili' && ['keyword_search', 'account_videos'].includes(form.target_type))
const defaultMinViews = () => supportsViews.value ? 10000 : 0
watch(() => form.platform, () => {
  if (!editingId.value) {
    form.target_type = availableTypes.value[0] ?? 'keyword_search'
    form.interval_minutes = form.platform === 'x' ? 60 : 30
    if (form.platform === 'x') form.community = 'ai'
  }
})
watch([() => form.target_type, () => form.community, supportsViews], () => { form.min_views = defaultMinViews() })

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
    const wasEditing = editingId.value !== null
    await api(wasEditing ? `/api/admin/collect-targets/${editingId.value}` : '/api/admin/collect-targets', {
      method: wasEditing ? 'PUT' : 'POST',
      body: {
        platform: form.platform,
        target_type: form.target_type,
        target_value: form.target_value.trim(),
        display_name: form.display_name.trim() || null,
        interval_minutes: Number(form.interval_minutes) || 30,
        co_terms: form.co_terms.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean),
        min_views: supportsViews.value ? Number(form.min_views) || 0 : 0,
        tier: form.tier,
        community: form.community,
      },
    })
    editingId.value = null
    toast.ok(wasEditing ? '采集目标已保存' : '采集目标已创建')
    form.target_value = ''
    form.display_name = ''
    form.co_terms = ''
    form.min_views = defaultMinViews()
    form.tier = 'general'
    await refresh()
  } catch (exc: any) {
    formError.value = exc?.statusCode === 409 ? '该目标已存在' : `保存失败：${exc?.data?.detail ?? exc?.message ?? exc}`
  } finally {
    creating.value = false
  }
}

async function edit(target: CollectTarget) {
  editingId.value = target.id
  formError.value = null
  Object.assign(form, {
    platform: target.platform, target_type: target.target_type, target_value: target.target_value,
    display_name: target.display_name || '', interval_minutes: target.interval_minutes,
    co_terms: (target.co_terms || []).join('，'), min_views: target.min_views || 0,
    tier: target.tier, community: target.community,
  })
  await nextTick()
  form.min_views = target.min_views || 0
  document.getElementById('collect-target-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function cancelEdit() {
  editingId.value = null
  form.target_value = ''
  form.display_name = ''
  form.co_terms = ''
  formError.value = null
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
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '操作失败，请重试')
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
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '删除失败，请重试')
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
    <div class="wall-heading">
      <h1 class="text-2xl font-bold">采集目标</h1>
      <p class="mt-0.5 text-sm text-muted">
        关键词、账号与频率配置；调度器按频率自动投递采集任务。
      </p>
    </div>

    <div v-if="biliSession" class="panel flex flex-wrap items-center justify-between gap-3 px-5 py-4" aria-live="polite">
      <div>
        <p class="text-sm font-medium" :class="biliSession.state === 'logged_in' ? 'text-ok' : 'text-warn'">{{ biliSession.message }}</p>
        <p v-if="biliSession.checked_at" class="mono mt-1 text-xs text-muted">检测时间 {{ fmtTime(biliSession.checked_at) }}</p>
      </div>
      <button class="btn-primary px-3 py-2 text-sm" :disabled="checkingBili" @click="loadBiliSession(true)">
        {{ checkingBili ? '检测中…' : '检测B站登录态' }}
      </button>
    </div>

    <form id="collect-target-form" class="panel grid gap-3 p-5 sm:grid-cols-[9rem_9rem_1fr_6rem_auto]" @submit.prevent="create">
      <p v-if="editingId" class="col-span-full text-sm font-medium">编辑采集目标 #{{ editingId }}</p>
      <select v-model="form.platform" aria-label="平台" :disabled="editingId !== null" class="select px-3 py-2 text-sm">
        <option v-for="(label, value) in platformLabels" :key="value" :value="value">{{ label }}</option>
      </select>
      <select v-model="form.target_type" aria-label="采集类型" :disabled="editingId !== null" class="select px-3 py-2 text-sm">
        <option v-for="type in availableTypes" :key="type" :value="type">{{ targetTypeLabels[type] ?? type }}</option>
      </select>
      <input
        v-model="form.target_value"
        :placeholder="form.platform === 'x' ? '@账号 或 https://x.com/账号' : '关键词 / 账号 UID / 频道 ID（公告类可留占位）'"
        :aria-label="form.platform === 'x' ? 'X 账号' : '目标值'"
        class="input px-3 py-2 text-sm"
      >
      <input v-model.number="form.interval_minutes" :aria-label="form.platform === 'x' ? '基准采集间隔（分钟）' : '采集间隔（分钟）'" :title="form.platform === 'x' ? '每轮随机浮动 ±25%，至少间隔 30 分钟' : ''" type="number" :min="form.platform === 'x' ? 30 : 5" max="1440" class="input mono px-3 py-2 text-sm">
      <select v-model="form.tier" class="select px-3 py-2 text-sm sm:col-span-2" title="核心关键词：商品/联名等特定内容，全量入库强及时；泛关键词：社区泛话题，走共现词与热度门槛">
        <option value="core">准入分级：核心关键词（全量入库，强及时）</option>
        <option value="general">准入分级：泛关键词（走共现词与热度门槛）</option>
      </select>
      <select v-model="form.community" aria-label="所属分区" class="select px-3 py-2 text-sm" title="所属分区：决定内容与事件的归属">
        <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">分区：{{ c.label }}</option>
      </select>
      <input v-model="form.display_name" aria-label="显示名称" placeholder="显示名称（可选）" class="input px-3 py-2 text-sm sm:col-span-2">
      <input v-model="form.co_terms" placeholder="共现词（逗号分隔，如 键盘,开箱,发货；留空不过滤；核心分级不生效）" class="input px-3 py-2 text-sm sm:col-span-2">
      <label class="text-xs text-muted">{{ supportsViews ? '最低播放量（0 不过滤）' : '此目标没有播放量门槛' }}<input v-model.number="form.min_views" type="number" min="0" :disabled="!supportsViews" class="input mono mt-1 w-full px-3 py-2 text-sm disabled:opacity-50"></label>
      <button type="submit" class="btn-primary px-4 py-2" :disabled="creating || !form.target_value.trim()">
        {{ creating ? '保存中…' : editingId ? '保存修改' : '新增目标' }}
      </button>
      <button v-if="editingId" type="button" class="px-3 py-2 text-sm text-muted" :disabled="creating" @click="cancelEdit">取消编辑</button>
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
          :class="communityFilter === option[0] ? 'bg-blue-600 text-white' : 'bg-chip text-muted hover:text-ink-2'"
          @click="communityFilter = option[0]"
        >
          {{ option[1] }}
        </button>
      </div>
    </div>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-[850px] text-sm">
        <thead>
          <tr class="border-b border-line-soft text-left text-xs text-muted dark:border-line">
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
          <tr v-for="target in filteredTargets" :key="target.id" class="border-b border-line-soft last:border-0">
            <td class="px-3 py-2.5">
              <span class="font-medium">{{ target.display_name && target.display_name !== target.target_value ? target.display_name : target.target_value }}</span>
              <span v-if="target.display_name && target.display_name !== target.target_value" class="mono ml-1.5 text-xs text-faint">{{ target.target_value }}</span>
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5 text-ink-2">
              {{ platformLabels[target.platform] ?? target.platform }}
              <span class="text-faint">·</span>
              {{ targetTypeLabels[target.target_type] ?? target.target_type }}
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5">
              <span
                class="chip whitespace-nowrap"
                :class="target.community === 'blhx' ? 'bg-cyan-500/10 text-cyan-700 dark:bg-cyan-400/15 dark:text-cyan-300' : 'bg-chip text-muted'"
              >
                {{ communityLabels[target.community] ?? target.community }}
              </span>
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5">
              <button
                class="chip cursor-pointer whitespace-nowrap transition-colors"
                :class="target.tier === 'core' ? 'bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300' : 'bg-chip text-muted'"
                :title="target.tier === 'core' ? '核心关键词：全量入库，强及时。点击切换为泛关键词' : '泛关键词：走共现词与热度门槛。点击切换为核心关键词'"
                :disabled="busyId === target.id"
                @click="toggleTier(target)"
              >
                {{ target.tier === 'core' ? '核心' : '泛关键词' }}
              </button>
            </td>
            <td class="mono whitespace-nowrap px-2.5 py-2.5 text-xs text-muted">{{ target.min_views ? `≥${target.min_views}` : '—' }}</td>
            <td class="mono whitespace-nowrap px-2.5 py-2.5 text-ink-2">{{ target.platform === 'x' ? '约 ' : '' }}{{ target.interval_minutes }}min{{ target.platform === 'x' ? ' · 随机' : '' }}</td>
            <td class="max-w-40 truncate px-3 py-2.5 text-xs text-faint" :title="target.last_error ?? ''">
              {{ target.last_error ?? '—' }}
            </td>
            <td class="whitespace-nowrap px-3 py-2.5 text-right">
              <button class="rounded px-1.5 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === target.id || creating" @click="edit(target)">编辑</button>
              <button class="rounded px-1.5 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === target.id" @click="runNow(target)">立即采集</button>
              <button class="rounded px-1.5 py-1 text-xs text-muted hover:underline" :disabled="busyId === target.id" @click="toggle(target)">
                {{ target.enabled ? '停用' : '启用' }}
              </button>
              <button class="rounded px-1.5 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busyId === target.id" @click="remove(target)">删除</button>
            </td>
          </tr>
          <tr v-if="targets.length === 0">
            <td colspan="8" class="px-4 py-10 text-center text-sm text-faint">暂无目标</td>
          </tr>
        </tbody>
      </table>
    </div>

    <section class="panel overflow-hidden">
      <div class="flex items-center justify-between px-4 py-3">
        <h2 class="text-sm font-semibold">最近采集运行</h2>
        <p class="text-xs text-faint">抓取 → 过滤（分级/门槛） → 入库</p>
      </div>
      <table class="w-full text-sm">
        <tbody>
          <tr v-for="run in runs" :key="run.id" class="border-t border-line-soft text-xs">
            <td class="px-4 py-2 text-ink-2">{{ targetName(run.target_id) }}</td>
            <td class="mono px-4 py-2 text-faint">{{ fmtTime(run.started_at) }}</td>
            <td class="px-4 py-2">
              <span class="chip" :class="run.status === 'success' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' : 'bg-chip text-muted'">{{ run.status === 'skipped' ? '冷却跳过' : run.status }}</span>
            </td>
            <td class="mono px-4 py-2 text-muted">
              抓取 {{ run.items_fetched ?? run.items_found ?? '—' }} · 过滤 {{ run.items_filtered ?? 0 }} · 入库 {{ run.items_new ?? 0 }}
            </td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="4" class="px-4 py-6 text-center text-sm text-faint">暂无运行记录。</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
