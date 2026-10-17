<script setup lang="ts">
import { fmtTime, intentTierLabels, ruleActionLabels, ruleChannelLabels, sourceTierLabels } from '~/utils/admin-types'
import { Filter, Plus, RefreshCw } from 'lucide-vue-next'
import type {
  RuleAction,
  RuleEngineItem,
  RuleHitItem,
  RulesConfig,
  VolumeBaselineItem,
} from '~/utils/admin-types'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '规则引擎 · AniSignal 管理看板' })

const { api } = useAdminApi()
const toast = useAdminToast()

type Tab = 'rules' | 'hits' | 'volume' | 'config'
const tab = ref<Tab>('rules')

// ---------- 规则列表 ----------

const items = ref<RuleEngineItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const error = ref<string | null>(null)
const qInput = ref('')
const q = ref('')

async function refreshRules() {
  loading.value = true
  error.value = null
  try {
    const query: Record<string, string | number> = { page: page.value, page_size: pageSize }
    if (q.value.trim()) query.q = q.value.trim()
    const data = await api<{ items: RuleEngineItem[], total: number }>('/api/admin/rules-engine', { query })
    items.value = data.items
    total.value = data.total
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    loading.value = false
  }
}
watch(page, refreshRules)
watch(tab, (next) => {
  if (next === 'rules') refreshRules()
})

function applySearch() {
  page.value = 1
  q.value = qInput.value
  refreshRules()
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

// ---------- 新建 / 编辑 ----------

const emptyDraft = () => ({
  name: '',
  enabled: true,
  priority: 100,
  keyword_expr: '',
  platforms: '',
  communities: '',
  intent_tiers: [] as string[],
  note: '',
  actions: [] as RuleAction[],
})

const draft = ref(emptyDraft())
const editingId = ref<number | null>(null)
const saving = ref(false)
const formError = ref<string | null>(null)
const preview = ref<{ scanned: number, matched: number, parsed_expr: string, items: any[] } | null>(null)
const previewing = ref(false)

function splitList(value: string): string[] {
  return value.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean)
}

function startCreate() {
  editingId.value = null
  draft.value = emptyDraft()
  formError.value = null
  preview.value = null
}

function startEdit(rule: RuleEngineItem) {
  editingId.value = rule.id
  draft.value = {
    name: rule.name,
    enabled: rule.enabled,
    priority: rule.priority,
    keyword_expr: rule.keyword_expr ?? '',
    platforms: (rule.platforms ?? []).join(' '),
    communities: (rule.communities ?? []).join(' '),
    intent_tiers: [...(rule.intent_tiers ?? [])],
    note: rule.note ?? '',
    actions: (rule.actions ?? []).map((a) => ({ ...a })),
  }
  formError.value = null
  preview.value = null
}

function addAction(type: string) {
  if (type === 'tag') draft.value.actions.push({ type, value: '' })
  else if (type === 'notify') draft.value.actions.push({ type, channels: ['qq'] })
  else draft.value.actions.push({ type })
}

function removeAction(index: number) {
  draft.value.actions.splice(index, 1)
}

function toggleChannel(action: RuleAction, channel: string) {
  const channels = action.channels ?? []
  const at = channels.indexOf(channel)
  if (at >= 0) channels.splice(at, 1)
  else channels.push(channel)
  action.channels = channels
}

function toggleTier(tier: string) {
  const at = draft.value.intent_tiers.indexOf(tier)
  if (at >= 0) draft.value.intent_tiers.splice(at, 1)
  else draft.value.intent_tiers.push(tier)
}

function bodyOf() {
  return {
    name: draft.value.name.trim(),
    enabled: draft.value.enabled,
    priority: Number(draft.value.priority),
    keyword_expr: draft.value.keyword_expr.trim() || null,
    platforms: splitList(draft.value.platforms),
    communities: splitList(draft.value.communities),
    intent_tiers: draft.value.intent_tiers,
    note: draft.value.note.trim() || null,
    actions: draft.value.actions.map((a) => ({
      type: a.type,
      value: a.type === 'tag' ? (a.value ?? '').trim() || null : null,
      channels: a.type === 'notify' ? (a.channels ?? []) : [],
    })),
  }
}

async function saveRule() {
  if (saving.value) return
  if (!draft.value.name.trim()) {
    formError.value = '规则名称不能为空'
    return
  }
  const badTag = draft.value.actions.find((a) => a.type === 'tag' && !(a.value ?? '').trim())
  if (badTag) {
    formError.value = '打标签动作必须填标签值'
    return
  }
  saving.value = true
  formError.value = null
  try {
    const body = bodyOf()
    if (editingId.value === null) {
      await api<RuleEngineItem>('/api/admin/rules-engine', { method: 'POST', body })
      toast.ok('规则已创建')
    } else {
      await api<RuleEngineItem>(`/api/admin/rules-engine/${editingId.value}`, { method: 'PUT', body })
      toast.ok('规则已保存')
    }
    startCreate()
    await refreshRules()
  } catch (exc: any) {
    formError.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    saving.value = false
  }
}

async function previewRule() {
  if (previewing.value) return
  previewing.value = true
  formError.value = null
  try {
    preview.value = await api('/api/admin/rules-engine/preview', { method: 'POST', body: bodyOf() })
  } catch (exc: any) {
    formError.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    previewing.value = false
  }
}

async function toggleEnabled(rule: RuleEngineItem) {
  try {
    await api(`/api/admin/rules-engine/${rule.id}`, { method: 'PUT', body: { enabled: !rule.enabled } })
    rule.enabled = !rule.enabled
    toast.ok(rule.enabled ? '规则已启用' : '规则已停用')
  } catch (exc: any) {
    toast.err(`操作失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  }
}

async function removeRule(rule: RuleEngineItem) {
  if (!confirm(`删除规则「${rule.name}」？其命中台账会一并删除。`)) return
  try {
    await api(`/api/admin/rules-engine/${rule.id}`, { method: 'DELETE' })
    items.value = items.value.filter((i) => i.id !== rule.id)
    total.value = Math.max(0, total.value - 1)
    toast.ok(`已删除「${rule.name}」`)
  } catch (exc: any) {
    toast.err(`删除失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  }
}

// ---------- 命中台账 ----------

const hits = ref<RuleHitItem[]>([])
const hitsTotal = ref(0)
const hitsPage = ref(1)
const hitsLoading = ref(false)
const hitsError = ref<string | null>(null)
const hitsRuleId = ref('')

async function refreshHits() {
  hitsLoading.value = true
  hitsError.value = null
  try {
    const query: Record<string, string | number> = { page: hitsPage.value, page_size: 20 }
    if (hitsRuleId.value) query.rule_id = Number(hitsRuleId.value)
    const data = await api<{ items: RuleHitItem[], total: number }>('/api/admin/rules-engine/hits', { query })
    hits.value = data.items
    hitsTotal.value = data.total
  } catch (exc: any) {
    hitsError.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    hitsLoading.value = false
  }
}
watch(hitsPage, refreshHits)

// ---------- 声量基线 ----------

const baselines = ref<VolumeBaselineItem[]>([])
const volumeTotal = ref(0)
const volumeLoading = ref(false)
const volumeError = ref<string | null>(null)
const volumeStatus = ref('')
const sweeping = ref(false)

async function refreshVolume() {
  volumeLoading.value = true
  volumeError.value = null
  try {
    const query: Record<string, string | number> = { page: 1, page_size: 50 }
    if (volumeStatus.value) query.status = volumeStatus.value
    const data = await api<{ items: VolumeBaselineItem[], total: number }>('/api/admin/rules-engine/volume', { query })
    baselines.value = data.items
    volumeTotal.value = data.total
  } catch (exc: any) {
    volumeError.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    volumeLoading.value = false
  }
}
watch(volumeStatus, refreshVolume)

async function sweepNow() {
  if (sweeping.value) return
  sweeping.value = true
  try {
    const result = await api<{ groups: number, windows: number, anomalies: number, notified: number }>(
      '/api/admin/rules-engine/volume/sweep',
      { method: 'POST' },
    )
    toast.ok(`扫描完成：${result.windows} 个窗口，${result.anomalies} 个异常，推送 ${result.notified} 条`)
    await refreshVolume()
  } catch (exc: any) {
    toast.err(`扫描失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  } finally {
    sweeping.value = false
  }
}

// ---------- 配置 ----------

const config = ref<RulesConfig | null>(null)
const configLoading = ref(false)
const configSaving = ref(false)
const configError = ref<string | null>(null)

async function refreshConfig() {
  configLoading.value = true
  configError.value = null
  try {
    config.value = await api<RulesConfig>('/api/admin/rules-engine/config')
  } catch (exc: any) {
    configError.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    configLoading.value = false
  }
}

async function saveConfig() {
  if (!config.value || configSaving.value) return
  configSaving.value = true
  configError.value = null
  try {
    config.value = await api<RulesConfig>('/api/admin/rules-engine/config', {
      method: 'PUT',
      body: { ...config.value },
    })
    toast.ok('配置已保存')
  } catch (exc: any) {
    configError.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    configSaving.value = false
  }
}

async function refreshTiers() {
  try {
    const result = await api<{ scanned: number, changed: number, by_tier: Record<string, number> }>(
      '/api/admin/rules-engine/source-tiers/refresh',
      { method: 'POST' },
    )
    const detail = Object.entries(result.by_tier).map(([k, v]) => `${sourceTierLabels[k] ?? k} ${v}`).join('，')
    toast.ok(`已扫描 ${result.scanned} 个来源，${result.changed} 个档位变化${detail ? `：${detail}` : ''}`)
  } catch (exc: any) {
    toast.err(`重算失败：${exc?.data?.detail ?? exc?.message ?? exc}`)
  }
}

onMounted(() => {
  refreshRules()
  refreshConfig()
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">规则引擎</h1>
        <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
          命中入库后执行「条件 → 动作」：布尔关键词 / 来源 / 重要性档命中后打标签、转事件、走通知渠道或标记已读；声量超过基线自动发热度预警。
        </p>
      </div>
      <button class="btn-secondary px-3 py-2 text-sm" :disabled="loading" @click="refreshRules(); refreshVolume(); refreshConfig()">
        <RefreshCw :size="14" class="mr-1 inline" /> 刷新
      </button>
    </div>

    <nav class="flex flex-wrap gap-1 border-b border-slate-900/5 text-sm dark:border-white/10">
      <button
        v-for="entry in ([['rules', '规则'], ['hits', '命中台账'], ['volume', '声量基线'], ['config', '阈值与信源分级']] as const)"
        :key="entry[0]"
        class="min-h-9 rounded-t px-3 py-2 transition-colors"
        :class="tab === entry[0]
          ? 'border-b-2 border-blue-600 font-medium text-blue-700 dark:border-blue-400 dark:text-blue-300'
          : 'text-slate-500 hover:text-slate-800 dark:text-zinc-400 dark:hover:text-zinc-100'"
        @click="tab = entry[0]; if (entry[0] === 'hits') refreshHits(); if (entry[0] === 'volume') refreshVolume(); if (entry[0] === 'config') refreshConfig()"
      >
        {{ entry[1] }}
      </button>
    </nav>

    <!-- ============ 规则 ============ -->
    <template v-if="tab === 'rules'">
      <section class="panel p-4">
        <div class="flex flex-wrap items-center gap-2">
          <form class="flex items-center gap-2" @submit.prevent="applySearch">
            <input v-model="qInput" placeholder="规则名 / 表达式" class="input w-56 px-3 py-2 text-sm" />
            <button type="submit" class="btn-primary px-4 py-2 text-sm" :disabled="loading">搜索</button>
          </form>
          <button class="btn-secondary ml-auto px-3 py-2 text-sm" @click="startCreate">
            <Plus :size="14" class="mr-1 inline" /> 新建规则
          </button>
          <span class="text-xs text-slate-400 dark:text-zinc-500">共 {{ total }} 条规则（按优先级升序执行）</span>
        </div>

        <p v-if="error" class="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

        <div class="mt-3 overflow-x-auto">
          <table class="w-full min-w-3xl text-sm">
            <thead>
              <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
                <th class="px-3 py-2.5 font-medium">规则</th>
                <th class="px-3 py-2.5 font-medium">实际匹配</th>
                <th class="px-3 py-2.5 font-medium">限定</th>
                <th class="px-3 py-2.5 font-medium">动作</th>
                <th class="px-3 py-2.5 font-medium">命中</th>
                <th class="px-3 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="rule in items"
                :key="rule.id"
                class="border-b border-slate-900/5 last:border-0 dark:border-white/5"
                :class="!rule.enabled ? 'opacity-60' : ''"
              >
                <td class="px-3 py-2.5">
                  <span class="font-medium">{{ rule.name }}</span>
                  <span class="mono ml-1.5 text-xs text-slate-400">P{{ rule.priority }}</span>
                  <p v-if="rule.note" class="mt-0.5 text-xs text-slate-400">{{ rule.note }}</p>
                  <p v-if="rule.last_error" class="mt-0.5 text-xs text-red-500 dark:text-red-400">最近失败：{{ rule.last_error }}</p>
                </td>
                <td class="mono px-3 py-2.5 text-xs text-slate-600 dark:text-zinc-300">{{ rule.parsed_expr }}</td>
                <td class="px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">
                  <span v-if="rule.platforms.length" class="chip mr-1">{{ rule.platforms.join(' ') }}</span>
                  <span v-if="rule.communities.length" class="chip mr-1">{{ rule.communities.join(' ') }}</span>
                  <span v-if="rule.intent_tiers.length" class="chip">
                    {{ rule.intent_tiers.map((t) => intentTierLabels[t] ?? t).join('/') }}
                  </span>
                  <span v-if="!rule.platforms.length && !rule.communities.length && !rule.intent_tiers.length" class="text-slate-400">不限</span>
                </td>
                <td class="px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">
                  <span v-for="(action, index) in rule.actions" :key="index" class="mr-1.5 whitespace-nowrap">
                    <span class="chip">{{ ruleActionLabels[action.type] ?? action.type }}</span>
                    <span v-if="action.type === 'tag' && action.value" class="ml-1 text-slate-600 dark:text-zinc-300">{{ action.value }}</span>
                    <span v-if="action.type === 'notify'" class="ml-1 text-slate-600 dark:text-zinc-300">
                      {{ (action.channels ?? []).map((c) => ruleChannelLabels[c] ?? c).join('/') }}
                    </span>
                  </span>
                  <span v-if="!rule.actions.length" class="text-slate-400">无</span>
                </td>
                <td class="mono px-3 py-2.5 text-slate-500 dark:text-zinc-400">
                  {{ rule.hit_count }}
                  <span class="ml-1 text-[11px] text-slate-400">{{ fmtTime(rule.last_hit_at) }}</span>
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 text-right">
                  <button class="min-h-9 rounded px-2 text-xs text-blue-600 hover:underline dark:text-blue-300" @click="startEdit(rule)">编辑</button>
                  <button
                    class="min-h-9 rounded px-2 text-xs underline-offset-2 hover:underline"
                    :class="rule.enabled ? 'text-amber-600 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300'"
                    @click="toggleEnabled(rule)"
                  >{{ rule.enabled ? '停用' : '启用' }}</button>
                  <button class="min-h-9 rounded px-2 text-xs text-red-500 hover:underline" @click="removeRule(rule)">删除</button>
                </td>
              </tr>
              <tr v-if="!items.length && !loading">
                <td colspan="6" class="px-3 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">还没有规则，先建一条试试</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-900/5 px-3 py-2.5 text-xs text-slate-400 dark:border-white/10 dark:text-zinc-500">
          <span>共 {{ total }} 条规则</span>
          <div class="flex items-center gap-3">
            <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="page--">« 上一页</button>
            <span class="mono">{{ page }} / {{ totalPages }}</span>
            <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="page++">下一页 »</button>
          </div>
        </div>
      </section>

      <!-- 新建 / 编辑 -->
      <section class="panel p-4">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-50">
          {{ editingId === null ? '新建规则' : `编辑规则 #${editingId}` }}
        </h2>

        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="text-xs text-slate-500 dark:text-zinc-400">
            规则名称
            <input v-model="draft.name" class="input mt-1 w-full px-3 py-2 text-sm" placeholder="例如：联名预警" />
          </label>
          <label class="text-xs text-slate-500 dark:text-zinc-400">
            优先级（越小越先执行）
            <input v-model.number="draft.priority" type="number" min="0" max="1000" class="input mono mt-1 w-full px-3 py-2 text-sm" />
          </label>
        </div>

        <label class="mt-3 block text-xs text-slate-500 dark:text-zinc-400">
          关键词表达式（支持 <code class="mono">AND</code> / <code class="mono">OR</code> / <code class="mono">NOT</code>、括号、引号短语、全角符号；留空表示不限关键词）
          <input v-model="draft.keyword_expr" class="input mono mt-1 w-full px-3 py-2 text-sm" placeholder="鸣潮 AND (联名 OR 周边)" />
        </label>

        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="text-xs text-slate-500 dark:text-zinc-400">
            限定平台（空格分隔，留空=全部）
            <input v-model="draft.platforms" class="input mono mt-1 w-full px-3 py-2 text-sm" placeholder="bilibili miyoushe" />
          </label>
          <label class="text-xs text-slate-500 dark:text-zinc-400">
            限定分区（空格分隔，留空=全部）
            <input v-model="draft.communities" class="input mono mt-1 w-full px-3 py-2 text-sm" placeholder="wuwa blhx" />
          </label>
        </div>

        <div class="mt-3">
          <p class="text-xs text-slate-500 dark:text-zinc-400">限定重要性档（不选=全部）</p>
          <div class="mt-1 flex flex-wrap gap-2">
            <button
              v-for="(label, tier) in intentTierLabels"
              :key="tier"
              class="chip cursor-pointer"
              :class="draft.intent_tiers.includes(tier) ? 'bg-blue-600/10 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300' : ''"
              @click="toggleTier(tier)"
            >{{ label }}</button>
          </div>
        </div>

        <div class="mt-3">
          <div class="flex flex-wrap items-center gap-2">
            <p class="text-xs text-slate-500 dark:text-zinc-400">动作</p>
            <button class="chip cursor-pointer" @click="addAction('tag')">+ 打标签</button>
            <button class="chip cursor-pointer" @click="addAction('promote_event')">+ 转为事件</button>
            <button class="chip cursor-pointer" @click="addAction('notify')">+ 通知渠道</button>
            <button class="chip cursor-pointer" @click="addAction('mark_read')">+ 标记已读</button>
          </div>
          <ul class="mt-2 space-y-2">
            <li v-for="(action, index) in draft.actions" :key="index" class="flex flex-wrap items-center gap-2 rounded-md bg-slate-900/[0.03] px-3 py-2 dark:bg-white/[0.04]">
              <span class="chip">{{ ruleActionLabels[action.type] ?? action.type }}</span>
              <input
                v-if="action.type === 'tag'"
                v-model="action.value"
                placeholder="标签值，如 联名"
                class="input w-40 px-2 py-1 text-sm"
              />
              <template v-if="action.type === 'notify'">
                <button
                  v-for="(label, channel) in ruleChannelLabels"
                  :key="channel"
                  class="chip cursor-pointer"
                  :class="(action.channels ?? []).includes(channel) ? 'bg-blue-600/10 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300' : ''"
                  @click="toggleChannel(action, channel)"
                >{{ label }}</button>
                <span class="text-xs text-slate-400">不选默认走 QQ 机器人</span>
              </template>
              <span v-else-if="action.type === 'promote_event'" class="text-xs text-slate-400">命中内容送进事件聚合</span>
              <span v-else-if="action.type === 'mark_read'" class="text-xs text-slate-400">在规则自己的视图里标记已读</span>
              <button class="ml-auto text-xs text-red-500 hover:underline" @click="removeAction(index)">移除</button>
            </li>
          </ul>
        </div>

        <label class="mt-3 block text-xs text-slate-500 dark:text-zinc-400">
          规则说明（会带进通知正文）
          <input v-model="draft.note" class="input mt-1 w-full px-3 py-2 text-sm" placeholder="可选" />
        </label>

        <p v-if="formError" class="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ formError }}</p>

        <div v-if="preview" class="mt-3 rounded-md bg-blue-500/5 px-3 py-2 text-xs text-slate-600 dark:text-zinc-300">
          试跑：扫描 {{ preview.scanned }} 条最近内容，命中 {{ preview.matched }} 条
          <span class="mono ml-1 text-slate-400">（{{ preview.parsed_expr }}）</span>
          <ul v-if="preview.items.length" class="mt-1 space-y-0.5">
            <li v-for="hit in preview.items.slice(0, 5)" :key="hit.content_id">
              <span class="mono text-slate-400">#{{ hit.content_id }}</span> {{ hit.title }}
            </li>
          </ul>
        </div>

        <div class="mt-3 flex flex-wrap gap-2">
          <button class="btn-primary px-4 py-2 text-sm" :disabled="saving" @click="saveRule">
            {{ saving ? '保存中…' : (editingId === null ? '创建规则' : '保存修改') }}
          </button>
          <button class="btn-secondary px-4 py-2 text-sm" :disabled="previewing" @click="previewRule">
            <Filter :size="14" class="mr-1 inline" /> 试跑条件
          </button>
          <button v-if="editingId !== null" class="btn-secondary px-4 py-2 text-sm" @click="startCreate">取消编辑</button>
        </div>
      </section>
    </template>

    <!-- ============ 命中台账 ============ -->
    <section v-else-if="tab === 'hits'" class="panel p-4">
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="hitsRuleId" class="select px-3 py-2 text-sm" @change="hitsPage = 1; refreshHits()">
          <option value="">全部规则</option>
          <option v-for="rule in items" :key="rule.id" :value="String(rule.id)">{{ rule.name }}</option>
        </select>
        <span class="ml-auto text-xs text-slate-400 dark:text-zinc-500">共 {{ hitsTotal }} 条命中记录</span>
      </div>

      <p v-if="hitsError" class="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ hitsError }}</p>

      <div class="mt-3 overflow-x-auto">
        <table class="w-full min-w-3xl text-sm">
          <thead>
            <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
              <th class="px-3 py-2.5 font-medium">时间</th>
              <th class="px-3 py-2.5 font-medium">规则</th>
              <th class="px-3 py-2.5 font-medium">内容</th>
              <th class="px-3 py-2.5 font-medium">结果</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="hit in hits"
              :key="hit.id"
              class="border-b border-slate-900/5 last:border-0 dark:border-white/5"
            >
              <td class="whitespace-nowrap px-3 py-2.5 text-xs text-slate-400 dark:text-zinc-500">{{ fmtTime(hit.created_at) }}</td>
              <td class="px-3 py-2.5">{{ hit.rule_name ?? `#${hit.rule_id}` }}</td>
              <td class="px-3 py-2.5">
                <span class="mono text-xs text-slate-400">#{{ hit.content_id }}</span>
                <span class="ml-1 text-slate-600 dark:text-zinc-300">{{ hit.content_title ?? '（已删除）' }}</span>
              </td>
              <td class="px-3 py-2.5 text-xs">
                <span v-if="hit.error" class="text-red-500 dark:text-red-400">失败：{{ hit.error }}</span>
                <span v-else class="text-slate-500 dark:text-zinc-400">{{ hit.detail }}</span>
              </td>
            </tr>
            <tr v-if="!hits.length && !hitsLoading">
              <td colspan="4" class="px-3 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">还没有命中记录</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="hitsTotal > 20" class="flex items-center justify-between border-t border-slate-900/5 px-3 py-2.5 text-xs text-slate-400 dark:border-white/10 dark:text-zinc-500">
        <span>共 {{ hitsTotal }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="hitsPage <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="hitsPage--">« 上一页</button>
          <span class="mono">{{ hitsPage }}</span>
          <button :disabled="hits.length < 20" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="hitsPage++">下一页 »</button>
        </div>
      </div>
    </section>

    <!-- ============ 声量基线 ============ -->
    <section v-else-if="tab === 'volume'" class="panel p-4">
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="volumeStatus" class="select px-3 py-2 text-sm">
          <option value="">全部状态</option>
          <option value="normal">正常</option>
          <option value="anomaly">异常预警</option>
        </select>
        <button class="btn-secondary ml-auto px-3 py-2 text-sm" :disabled="sweeping" @click="sweepNow">
          <RefreshCw :size="14" class="mr-1 inline" /> {{ sweeping ? '扫描中…' : '立即扫描' }}
        </button>
        <span class="text-xs text-slate-400 dark:text-zinc-500">共 {{ volumeTotal }} 个窗口</span>
      </div>

      <p v-if="volumeError" class="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ volumeError }}</p>

      <div class="mt-3 overflow-x-auto">
        <table class="w-full min-w-3xl text-sm">
          <thead>
            <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
              <th class="px-3 py-2.5 font-medium">监控分组</th>
              <th class="px-3 py-2.5 font-medium">窗口</th>
              <th class="px-3 py-2.5 font-medium">命中</th>
              <th class="px-3 py-2.5 font-medium">基线</th>
              <th class="px-3 py-2.5 font-medium">倍数</th>
              <th class="px-3 py-2.5 font-medium">状态</th>
              <th class="px-3 py-2.5 font-medium">已通知</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in baselines"
              :key="row.id"
              class="border-b border-slate-900/5 last:border-0 dark:border-white/5"
            >
              <td class="px-3 py-2.5">
                <span class="font-medium">{{ row.keyword }}</span>
                <span class="mono ml-1.5 text-xs text-slate-400">{{ row.platform }}</span>
              </td>
              <td class="px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">
                {{ fmtTime(row.window_started_at) }}
                <span class="ml-1 text-slate-400">{{ row.window_minutes }} 分钟</span>
              </td>
              <td class="mono px-3 py-2.5">{{ row.hit_count }}</td>
              <td class="mono px-3 py-2.5 text-xs text-slate-500 dark:text-zinc-400">
                {{ row.mean.toFixed(1) }} ±{{ row.stddev.toFixed(1) }}
                <span class="ml-1 text-slate-400">({{ row.samples }} 窗)</span>
              </td>
              <td class="mono px-3 py-2.5 text-xs">
                <span v-if="row.ratio !== null" :class="row.ratio >= row.multiplier ? 'text-red-500 dark:text-red-400' : 'text-slate-500 dark:text-zinc-400'">
                  {{ row.ratio.toFixed(1) }}×
                </span>
                <span v-else class="text-slate-400">—</span>
                <span class="ml-1 text-slate-400">/ {{ row.multiplier }}×</span>
              </td>
              <td class="px-3 py-2.5">
                <span class="chip" :class="row.status === 'anomaly' ? 'bg-red-500/10 text-red-600 dark:bg-red-400/10 dark:text-red-300' : ''">
                  {{ row.status === 'anomaly' ? '热度预警' : '正常' }}
                </span>
              </td>
              <td class="px-3 py-2.5 text-xs text-slate-400 dark:text-zinc-500">{{ fmtTime(row.notified_at) }}</td>
            </tr>
            <tr v-if="!baselines.length && !volumeLoading">
              <td colspan="7" class="px-3 py-10 text-center text-sm text-slate-400 dark:text-zinc-500">还没有声量数据，采集几轮后再看</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============ 配置 ============ -->
    <section v-else class="panel p-4">
      <p v-if="configError" class="mb-3 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">{{ configError }}</p>
      <div v-if="config" class="grid gap-4 sm:grid-cols-2">
        <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-zinc-300">
          <input v-model="config.enabled" type="checkbox" class="size-4" />
          启用规则引擎与声量告警
        </label>

        <label class="text-xs text-slate-500 dark:text-zinc-400">
          异常倍数（命中数超过基线均值 N 倍即预警）
          <input v-model.number="config.anomaly_multiplier" type="number" min="1.5" max="20" step="0.5" class="input mono mt-1 w-full px-3 py-2 text-sm" />
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          统计窗口（分钟）
          <input v-model.number="config.window_minutes" type="number" min="5" max="1440" class="input mono mt-1 w-full px-3 py-2 text-sm" />
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          历史窗口数（滑动基线样本量）
          <input v-model.number="config.history_windows" type="number" min="3" max="168" class="input mono mt-1 w-full px-3 py-2 text-sm" />
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          最少样本数（不足不告警）
          <input v-model.number="config.min_samples" type="number" min="1" max="200" class="input mono mt-1 w-full px-3 py-2 text-sm" />
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          最少命中数（不足不告警）
          <input v-model.number="config.min_hits" type="number" min="1" max="10000" class="input mono mt-1 w-full px-3 py-2 text-sm" />
        </label>
        <label class="text-xs text-slate-500 dark:text-zinc-400">
          同一分组预警冷却（分钟）
          <input v-model.number="config.cooldown_minutes" type="number" min="0" max="1440" class="input mono mt-1 w-full px-3 py-2 text-sm" />
        </label>

        <div class="sm:col-span-2">
          <p class="text-xs text-slate-500 dark:text-zinc-400">信源分级阈值（按粉丝量）</p>
          <div class="mt-1 grid gap-3 sm:grid-cols-2">
            <label class="text-xs text-slate-500 dark:text-zinc-400">
              中权重起（粉丝数）
              <input v-model.number="config.source_tiers.mid" type="number" min="1" class="input mono mt-1 w-full px-3 py-2 text-sm" />
            </label>
            <label class="text-xs text-slate-500 dark:text-zinc-400">
              高权重起（粉丝数）
              <input v-model.number="config.source_tiers.high" type="number" min="1" class="input mono mt-1 w-full px-3 py-2 text-sm" />
            </label>
          </div>
          <p class="mt-1 text-xs text-slate-400 dark:text-zinc-500">档位会标注在通知标题里，也会用于高权重命中优先展示。</p>
        </div>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <button class="btn-primary px-4 py-2 text-sm" :disabled="configSaving || !config" @click="saveConfig">
          {{ configSaving ? '保存中…' : '保存配置' }}
        </button>
        <button class="btn-secondary px-4 py-2 text-sm" @click="refreshTiers">
          <RefreshCw :size="14" class="mr-1 inline" /> 重算全部来源档位
        </button>
      </div>
    </section>
  </div>
</template>
