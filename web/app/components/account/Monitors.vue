<script setup lang="ts">
import { Eye, EyeOff, Mail, MailX, Pause, Pencil, Play, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import type { CustomFeedRow } from '~/composables/useAccountCenter'
import { customKindLabel } from '~/utils/custom-feeds'
import { relativeTime, toTimestamp } from '~/utils/schedule'

// 定制监控：自建 B 站关键词 / UP 主、RSS/Atom 订阅、YouTube 频道抓取源。公开的会出现在"定制监控"频道，私有的仅自己可见。
const route = useRoute()
const center = useAccountCenter()
const { customFeeds, customQuota, customBusy, customError, customMessage, loading, errors } = center
const now = ref(Date.now())
const creating = ref(route.query.create === '1')

const KINDS = [
  { key: 'keyword', platform: 'bilibili', label: 'B 站关键词', fieldLabel: '搜索关键词', placeholder: '例如：独立游戏 发售、机械键盘 评测' },
  { key: 'uid', platform: 'bilibili', label: 'B 站 UP 主', fieldLabel: 'UP 主 UID', placeholder: '纯数字，空间主页地址里的那串数字' },
  { key: 'feed', platform: 'rss', label: 'RSS / Atom', fieldLabel: 'RSS / Atom 地址', placeholder: 'https://example.com/feed.xml' },
  { key: 'channel_rss', platform: 'youtube', label: 'YouTube 频道', fieldLabel: '频道 ID 或主页链接', placeholder: 'UC... 或 https://www.youtube.com/channel/UC...' },
] as const
type MonitorKind = typeof KINDS[number]['key']

const blank = () => ({ name: '', kind: 'keyword' as MonitorKind, target_value: '', min_views: 0, interval_minutes: 60, includeText: '', excludeText: '', push_enabled: false, is_public: false })
const form = reactive(blank())
const editing = ref<number | null>(null)
const draft = reactive({ name: '', target_value: '', min_views: 0, interval_minutes: 30, includeText: '', excludeText: '' })

const activeKindMeta = computed(() => KINDS.find(k => k.key === form.kind) ?? KINDS[0])
const kindFieldLabel = (kind: string) => KINDS.find(k => k.key === kind)?.fieldLabel ?? '监控目标'

const words = (value: string) => value.split(/[,，\n]+/).map(v => v.trim()).filter(Boolean)
const quotaFull = computed(() => customQuota.value.used >= customQuota.value.max_feeds)
const canCreate = computed(() => customQuota.value.admitted && !quotaFull.value)
const quotaPercent = computed(() => Math.min(100, (customQuota.value.used / Math.max(1, customQuota.value.max_feeds)) * 100))

watch(() => customQuota.value.min_interval_minutes, (min) => { form.interval_minutes = Math.max(form.interval_minutes, min) }, { immediate: true })

function openCreate() {
  creating.value = true
  customError.value = customMessage.value = ''
}
async function create() {
  const { includeText, excludeText, ...rest } = form
  const ok = await center.createCustom({
    ...rest,
    platform: activeKindMeta.value.platform,
    name: rest.name.trim(),
    target_value: rest.target_value.trim(),
    min_views: rest.kind === 'keyword' ? Number(rest.min_views) || 0 : 0,
    filters: { include: words(includeText), exclude: words(excludeText) },
  })
  if (ok) {
    Object.assign(form, blank(), { interval_minutes: customQuota.value.min_interval_minutes })
    creating.value = false
  }
}
function startEdit(row: CustomFeedRow) {
  editing.value = row.id
  Object.assign(draft, {
    name: row.name, target_value: row.target_value, min_views: row.min_views, interval_minutes: row.interval_minutes,
    includeText: (row.filters?.include ?? []).join('，'), excludeText: (row.filters?.exclude ?? []).join('，'),
  })
  customError.value = customMessage.value = ''
}
async function saveEdit(row: CustomFeedRow) {
  const { includeText, excludeText, ...rest } = draft
  const ok = await center.patchCustom(row, {
    ...rest, name: rest.name.trim(), target_value: rest.target_value.trim(),
    filters: { include: words(includeText), exclude: words(excludeText) },
  }, '监控已更新。')
  if (ok) editing.value = null
}
function remove(row: CustomFeedRow) {
  if (confirm(`删除监控「${row.name}」及其全部抓取记录？`)) center.removeCustom(row).then(() => { if (editing.value === row.id) editing.value = null })
}
function statusOf(row: CustomFeedRow) {
  if (row.status !== 'active') return { label: '管理员已停用', dot: 'bg-hot' }
  if (!row.enabled) return { label: '已暂停', dot: 'bg-faint' }
  return row.last_error ? { label: '采集失败', dot: 'bg-hot' } : { label: '采集中', dot: 'bg-ok' }
}
function lastCollected(row: CustomFeedRow) {
  const ts = toTimestamp(row.last_collected_at)
  return Number.isFinite(ts) ? `${relativeTime(ts, now.value)}采集` : '等待首次采集'
}
const action = 'inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] text-muted transition-colors hover:bg-chip hover:text-ink'
</script>

<template>
  <div id="custom-monitors" class="scroll-mt-24 space-y-5">
    <section class="panel p-5" aria-labelledby="monitors-title">
      <header class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 id="monitors-title" class="flex items-center gap-2 text-base font-bold text-ink">
            定制监控 <span class="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-ink">公测</span>
          </h2>
          <p class="mt-1 text-xs leading-relaxed text-muted">
            跟踪任意你感兴趣的内容：B 站关键词 / UP 主、RSS / Atom 订阅源或 YouTube 频道。公开的监控会展示在<NuxtLink to="/custom" class="link">定制监控频道</NuxtLink>，私有的仅自己可见。
          </p>
        </div>
        <button v-if="!creating" type="button" class="btn-primary inline-flex h-9 items-center gap-1 px-3.5" :disabled="!canCreate || loading.custom" @click="openCreate">
          <Plus :size="15" /> 新建监控
        </button>
      </header>

      <div class="mt-4">
        <div class="flex items-baseline justify-between text-xs text-muted">
          <span>已用 <span class="num text-sm font-bold text-ink">{{ customQuota.used }}</span>/{{ customQuota.max_feeds }} 个</span>
          <span>采集间隔至少 {{ customQuota.min_interval_minutes }} 分钟</span>
        </div>
        <div class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-chip">
          <div class="h-full rounded-full transition-[width]" :class="quotaFull ? 'bg-warn' : 'bg-brand'" :style="{ width: `${quotaPercent}%` }" />
        </div>
      </div>
      <UiNotice v-if="!loading.custom && !errors.custom && !customQuota.admitted" tone="warn" class="mt-4">当前账号还没有开通公测名额，请联系管理员。</UiNotice>

      <form v-if="creating" class="mt-5 rounded-xl border border-line bg-canvas/60 p-4" @submit.prevent="create">
        <div class="grid grid-cols-2 gap-1 rounded-xl bg-chip p-1 text-xs sm:grid-cols-4 sm:text-sm" role="radiogroup" aria-label="监控类型">
          <button
            v-for="kind in KINDS"
            :key="kind.key"
            type="button"
            role="radio"
            :aria-checked="form.kind === kind.key"
            class="h-8 rounded-[9px] px-2 transition-colors"
            :class="form.kind === kind.key ? 'bg-card font-semibold text-ink shadow-card' : 'text-muted hover:text-ink'"
            @click="form.kind = kind.key"
          >
            {{ kind.label }}
          </button>
        </div>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          <label class="text-xs font-medium text-ink-2">{{ activeKindMeta.fieldLabel }}
            <input
              v-model="form.target_value"
              required
              maxlength="128"
              :type="form.kind === 'feed' ? 'url' : 'text'"
              :inputmode="form.kind === 'uid' ? 'numeric' : undefined"
              :pattern="form.kind === 'uid' ? '[0-9]+' : undefined"
              :placeholder="activeKindMeta.placeholder"
              class="input mt-1.5 h-10 w-full font-normal"
            >
          </label>
          <label class="text-xs font-medium text-ink-2">监控名称
            <input v-model="form.name" required maxlength="64" placeholder="例如：独立游戏资讯 / 某博客订阅" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label v-if="form.kind === 'keyword'" class="text-xs font-medium text-ink-2">最低播放量 <span class="font-normal text-faint">（可选）</span>
            <input v-model.number="form.min_views" type="number" min="0" max="10000000" step="1" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label class="text-xs font-medium text-ink-2">采集间隔（分钟）
            <input v-model.number="form.interval_minutes" type="number" required :min="customQuota.min_interval_minutes" max="1440" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label class="text-xs font-medium text-ink-2">标题须包含任一词 <span class="font-normal text-faint">（逗号分隔，可选）</span>
            <input v-model="form.includeText" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label class="text-xs font-medium text-ink-2">排除词 <span class="font-normal text-faint">（逗号分隔，可选）</span>
            <input v-model="form.excludeText" class="input mt-1.5 h-10 w-full font-normal">
          </label>
        </div>
        <div class="mt-4 flex flex-wrap gap-x-5 gap-y-2">
          <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input v-model="form.is_public" type="checkbox" class="checkbox"> 公开到定制化频道</label>
          <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input v-model="form.push_enabled" type="checkbox" class="checkbox"> 新内容邮件推送</label>
        </div>
        <p class="mt-2 text-xs text-faint">推送需开启邮件通知；每个监控每天最多一封新内容摘要。</p>
        <div class="mt-4 flex gap-2">
          <button type="submit" class="btn-primary h-9 px-4" :disabled="customBusy || !canCreate">{{ customBusy ? '创建中…' : '创建监控' }}</button>
          <button type="button" class="h-9 rounded-[10px] px-3 text-sm text-muted hover:bg-chip hover:text-ink" @click="creating = false">取消</button>
        </div>
      </form>

      <UiNotice v-if="customError" tone="error" class="mt-4">{{ customError }}</UiNotice>
      <UiNotice v-if="customMessage" tone="ok" class="mt-4">{{ customMessage }}</UiNotice>
    </section>

    <p v-if="loading.custom && !customFeeds.length" role="status" class="px-1 text-sm text-faint">正在加载监控…</p>
    <UiNotice v-else-if="errors.custom" tone="warn">
      {{ errors.custom }}<button type="button" class="ml-2 font-semibold underline" @click="center.fetchCustom()">重试</button>
    </UiNotice>
    <div v-else-if="!customFeeds.length" class="rounded-2xl border border-dashed border-line px-5 py-10 text-center">
      <p class="text-sm text-muted">还没有定制监控。</p>
      <p class="mt-1 text-xs text-faint">{{ customQuota.admitted ? '点「新建监控」跟踪任意关键词、UP 主、RSS 订阅或 YouTube 频道。' : '开通公测名额后即可创建。' }}</p>
    </div>
    <ul v-else class="grid gap-4 md:grid-cols-2">
      <li v-for="row in customFeeds" :key="row.id" class="panel flex flex-col p-4">
        <template v-if="editing !== row.id">
          <div class="flex items-center gap-2 text-xs">
            <span class="rounded bg-chip px-1.5 py-0.5 font-medium text-ink-2">{{ customKindLabel(row.kind) }}</span>
            <span class="rounded px-1.5 py-0.5 font-medium" :class="row.is_public ? 'bg-brand-soft text-brand-ink' : 'bg-chip text-muted'">{{ row.is_public ? '公开' : '私有' }}</span>
            <span class="ml-auto flex items-center gap-1.5 text-muted">
              <span class="size-1.5 rounded-full" :class="statusOf(row).dot" aria-hidden="true" />{{ statusOf(row).label }}
            </span>
          </div>
          <NuxtLink :to="`/custom/${row.id}`" class="mt-2.5 line-clamp-1 text-base font-bold text-ink hover:text-brand-ink">{{ row.name }}</NuxtLink>
          <p class="mono mt-0.5 truncate text-xs text-faint">{{ row.kind === 'uid' ? `UID ${row.target_value}` : row.target_value }}</p>
          <p class="mt-3 flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
            <span><span class="num text-[15px] font-bold text-ink">{{ row.item_count }}</span> 条</span>
            <span>· 每 {{ row.interval_minutes }} 分钟</span>
            <span>· {{ lastCollected(row) }}</span>
          </p>
          <p v-if="row.enabled && row.last_error" class="mt-1 text-xs text-hot">上次采集失败：{{ row.last_error }}</p>
          <p v-if="row.min_views || row.filters?.include?.length || row.filters?.exclude?.length" class="mt-1 text-xs text-faint">
            <template v-if="row.min_views">播放 ≥ {{ row.min_views.toLocaleString('zh-CN') }}　</template>
            <template v-if="row.filters?.include?.length">包含：{{ row.filters.include.join('、') }}　</template>
            <template v-if="row.filters?.exclude?.length">排除：{{ row.filters.exclude.join('、') }}</template>
          </p>
          <div class="min-h-3 flex-1" aria-hidden="true" />
          <div class="flex flex-wrap items-center gap-0.5 border-t border-line-soft pt-2">
            <button type="button" :class="action" :disabled="customBusy || row.status !== 'active' || !row.enabled" :title="row.enabled ? '立即刷新采集' : '恢复监控后可手动抓取'" @click="center.triggerCustom(row)"><RefreshCw :size="13" />抓取</button>
            <button type="button" :class="action" :disabled="customBusy" @click="startEdit(row)"><Pencil :size="13" />编辑</button>
            <button type="button" :class="action" :disabled="customBusy" :aria-label="`${row.is_public ? '设为私有' : '公开'}：${row.name}`" @click="center.patchCustom(row, { is_public: !row.is_public }, row.is_public ? '已设为私有。' : '已公开到定制监控频道。')">
              <EyeOff v-if="row.is_public" :size="13" /><Eye v-else :size="13" />{{ row.is_public ? '设为私有' : '公开' }}
            </button>
            <button type="button" :class="action" :disabled="customBusy" :aria-label="`${row.push_enabled ? '关闭' : '开启'}邮件推送：${row.name}`" @click="center.patchCustom(row, { push_enabled: !row.push_enabled }, row.push_enabled ? '邮件推送已关闭。' : '邮件推送已开启。')">
              <MailX v-if="row.push_enabled" :size="13" /><Mail v-else :size="13" />{{ row.push_enabled ? '关推送' : '开推送' }}
            </button>
            <button type="button" :class="action" :disabled="customBusy || row.status !== 'active'" @click="center.patchCustom(row, { enabled: !row.enabled }, row.enabled ? '监控已暂停。' : '监控已恢复。')">
              <Pause v-if="row.enabled" :size="13" /><Play v-else :size="13" />{{ row.enabled ? '暂停' : '恢复' }}
            </button>
            <button type="button" :class="[action, 'ml-auto hover:bg-hot/10 hover:text-hot']" :disabled="customBusy" :aria-label="`删除监控：${row.name}`" @click="remove(row)"><Trash2 :size="13" /></button>
          </div>
        </template>

        <form v-else class="grid gap-3" @submit.prevent="saveEdit(row)">
          <p class="text-sm font-bold text-ink">编辑监控</p>
          <label class="text-xs font-medium text-ink-2">监控名称<input v-model="draft.name" required maxlength="64" class="input mt-1.5 h-9 w-full font-normal"></label>
          <label class="text-xs font-medium text-ink-2">{{ kindFieldLabel(row.kind) }}<input v-model="draft.target_value" required maxlength="128" :type="row.kind === 'feed' ? 'url' : 'text'" :pattern="row.kind === 'uid' ? '[0-9]+' : undefined" class="input mt-1.5 h-9 w-full font-normal"></label>
          <div class="grid grid-cols-2 gap-3">
            <label v-if="row.kind === 'keyword'" class="text-xs font-medium text-ink-2">最低播放量<input v-model.number="draft.min_views" type="number" required min="0" max="10000000" class="input mt-1.5 h-9 w-full font-normal"></label>
            <label class="text-xs font-medium text-ink-2">采集间隔（分钟）<input v-model.number="draft.interval_minutes" type="number" required :min="Math.min(row.interval_minutes, customQuota.min_interval_minutes)" max="1440" class="input mt-1.5 h-9 w-full font-normal"></label>
          </div>
          <label class="text-xs font-medium text-ink-2">包含任一词<input v-model="draft.includeText" class="input mt-1.5 h-9 w-full font-normal"></label>
          <label class="text-xs font-medium text-ink-2">排除词<input v-model="draft.excludeText" class="input mt-1.5 h-9 w-full font-normal"></label>
          <p class="text-xs text-faint">修改目标后会保留已采集的内容，之后按新目标采集。</p>
          <div class="flex gap-2">
            <button type="submit" class="btn-primary h-9 px-4" :disabled="customBusy">{{ customBusy ? '保存中…' : '保存' }}</button>
            <button type="button" class="h-9 rounded-[10px] px-3 text-sm text-muted hover:bg-chip hover:text-ink" @click="editing = null">取消</button>
          </div>
        </form>
      </li>
    </ul>
  </div>
</template>
