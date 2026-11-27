<script setup lang="ts">
import {
  ChevronDown,
  ExternalLink,
  Eye,
  EyeOff,
  Mail,
  MailX,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-vue-next'
import type { CustomFeedRow } from '~/composables/useAccountCenter'
import { apiErrorDetail } from '~/utils/api-errors'
import {
  customKindLabel,
  GH_RELEASES_MONITOR,
  STEAM_MONITOR,
  WEB_LIST_MONITOR,
  WEB_PAGE_MONITOR,
  XIAOHEIHE_ACCOUNT_MONITOR,
  XIAOHEIHE_KEYWORD_MONITOR,
  XIAOHEIHE_MONITOR,
} from '~/utils/custom-feeds'
import { MONITOR_GROUPS, MONITOR_KINDS, webExtractorLabel, type MonitorKindMeta } from '~/utils/lexicon'
import { relativeTime, toTimestamp } from '~/utils/schedule'

// 我的监控：按媒介分组选源 + 主路径与高级选项两层表单 + 卡片高频操作与溢出菜单。
const route = useRoute()
const center = useAccountCenter()
const { customFeeds, customQuota, customBusy, customError, customMessage, loading, errors } = center
const now = ref(Date.now())
const creating = ref(route.query.create === '1')
const advancedOpen = ref(false)
const editAdvancedOpen = ref(false)
const openMenuId = ref<number | null>(null)

type MonitorKind = MonitorKindMeta['key']

interface WebPreviewResult {
  mode: string
  url: string
  title: string
  extractor: string
  text_length: number
  text_preview: string
  media_count: number
  detected_feeds: { url: string; title: string; type: string }[]
  spa_warning: boolean
  items: { title: string; url: string; published_at: string | null }[]
}

const availableKinds = computed(() => MONITOR_KINDS.filter((k) => {
  if (k.key === XIAOHEIHE_KEYWORD_MONITOR.kind) return Boolean(customQuota.value.xiaoheihe_keyword_enabled)
  if (k.key === XIAOHEIHE_MONITOR.kind) return Boolean(customQuota.value.xiaoheihe_hot_enabled)
  return true
}))

const groupedKinds = computed(() =>
  MONITOR_GROUPS
    .map(group => ({ ...group, items: availableKinds.value.filter(k => k.group === group.key) }))
    .filter(group => group.items.length > 0),
)

const blank = () => ({
  name: '', kind: 'feed' as MonitorKind, target_value: '', min_views: 0, interval_minutes: 60,
  includeText: '', excludeText: '', selector: '', excludeSelector: '', linkPattern: '', ignoreText: '',
  includePrerelease: true,
  push_enabled: false, is_public: false,
})
const form = reactive(blank())
const editing = ref<number | null>(null)
const draft = reactive({
  name: '', target_value: '', min_views: 0, interval_minutes: 30,
  includeText: '', excludeText: '', selector: '', excludeSelector: '', linkPattern: '', ignoreText: '',
  includePrerelease: true,
})
const webPreview = ref<WebPreviewResult | null>(null)
const webPreviewing = ref(false)
const webPreviewError = ref('')

const activeKindMeta = computed(() => MONITOR_KINDS.find(k => k.key === form.kind) ?? MONITOR_KINDS[0]!)
const isHotPosts = computed(() => form.kind === XIAOHEIHE_MONITOR.kind)
const isKeywordKind = (kind: string) => kind === 'keyword' || kind === XIAOHEIHE_KEYWORD_MONITOR.kind
const isWebKind = (kind: string) => kind === WEB_PAGE_MONITOR.kind || kind === WEB_LIST_MONITOR.kind
const isGitReleaseKind = (kind: string) => kind === GH_RELEASES_MONITOR.kind
const isUrlKind = (kind: string) => kind === 'feed' || isWebKind(kind)
const existingXiaoheihe = computed(() => customFeeds.value.find(row => row.kind === XIAOHEIHE_MONITOR.kind))
const kindFieldLabel = (kind: string) => MONITOR_KINDS.find(k => k.key === kind)?.fieldLabel ?? '监控目标'

const words = (value: string) => value.split(/[,，\n]+/).map(v => v.trim()).filter(Boolean)
const quotaFull = computed(() => customQuota.value.used >= customQuota.value.max_feeds)
const canCreate = computed(() => customQuota.value.admitted && !quotaFull.value)
const quotaPercent = computed(() => Math.min(100, (customQuota.value.used / Math.max(1, customQuota.value.max_feeds)) * 100))

const advancedConfiguredCount = computed(() => {
  let count = 0
  if (form.includeText.trim()) count++
  if (form.excludeText.trim()) count++
  if (isKeywordKind(form.kind) && form.min_views > 0) count++
  if (isWebKind(form.kind)) {
    if (form.selector.trim()) count++
    if (form.excludeSelector.trim()) count++
    if (form.kind === WEB_LIST_MONITOR.kind && form.linkPattern.trim()) count++
    if (form.kind === WEB_PAGE_MONITOR.kind && form.ignoreText.trim()) count++
  }
  if (isGitReleaseKind(form.kind) && !form.includePrerelease) count++
  return count
})

watch(() => customQuota.value.min_interval_minutes, (min) => { form.interval_minutes = Math.max(form.interval_minutes, min) }, { immediate: true })

function onDocClick(event: MouseEvent) {
  if (openMenuId.value === null) return
  const target = event.target as HTMLElement | null
  if (!target?.closest('[data-monitor-menu]')) openMenuId.value = null
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') openMenuId.value = null
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})

function openCreate() {
  creating.value = true
  advancedOpen.value = false
  customError.value = customMessage.value = ''
  webPreview.value = null
  webPreviewError.value = ''
}
function selectKind(kind: MonitorKind) {
  form.kind = kind
  webPreview.value = null
  webPreviewError.value = ''
  if (kind === XIAOHEIHE_MONITOR.kind && !form.name.trim()) form.name = XIAOHEIHE_MONITOR.name
}
function buildFilters(kind: string, src: { includeText: string, excludeText: string, selector: string, excludeSelector: string, linkPattern: string, ignoreText: string, includePrerelease: boolean }) {
  const filters: Record<string, unknown> = { include: words(src.includeText), exclude: words(src.excludeText) }
  if (isWebKind(kind)) {
    if (src.selector.trim()) filters.selector = src.selector.trim()
    if (src.excludeSelector.trim()) filters.exclude_selector = src.excludeSelector.trim()
    if (kind === WEB_LIST_MONITOR.kind && src.linkPattern.trim()) filters.link_pattern = src.linkPattern.trim()
    if (kind === WEB_PAGE_MONITOR.kind && words(src.ignoreText).length) filters.ignore_patterns = words(src.ignoreText)
  }
  if (isGitReleaseKind(kind) && !src.includePrerelease) {
    filters.include_prerelease = false
  }
  return filters
}
async function previewWeb() {
  if (!isWebKind(form.kind) || !form.target_value.trim() || webPreviewing.value) return
  webPreviewing.value = true
  webPreviewError.value = ''
  try {
    webPreview.value = await $fetch<WebPreviewResult>('/api/account/web-page/preview', {
      method: 'POST',
      body: {
        url: form.target_value.trim(),
        mode: form.kind,
        web_config: {
          selector: form.selector.trim(),
          exclude_selector: form.excludeSelector.trim(),
          link_pattern: form.kind === WEB_LIST_MONITOR.kind ? form.linkPattern.trim() : '',
          ignore_patterns: form.kind === WEB_PAGE_MONITOR.kind ? words(form.ignoreText) : [],
        },
      },
    })
    if (!form.name.trim() && webPreview.value?.title) form.name = webPreview.value.title.slice(0, 64)
  } catch (exc) {
    webPreview.value = null
    webPreviewError.value = apiErrorDetail(exc, '网页解析失败，请检查地址后重试')
  } finally {
    webPreviewing.value = false
  }
}
function switchToDetectedFeed(feedUrl: string, feedTitle: string) {
  form.kind = 'feed'
  form.target_value = feedUrl
  if (!form.name.trim() && feedTitle) form.name = feedTitle.slice(0, 64)
  webPreview.value = null
  webPreviewError.value = ''
}
watch(() => [customQuota.value.xiaoheihe_hot_enabled, customQuota.value.xiaoheihe_keyword_enabled] as const, ([hotOk, kwOk]) => {
  if (route.query.source === STEAM_MONITOR.platform && form.kind === 'feed') {
    selectKind(STEAM_MONITOR.kind)
  } else if (route.query.source === XIAOHEIHE_MONITOR.platform && form.kind === 'feed') {
    selectKind(hotOk ? XIAOHEIHE_MONITOR.kind : (kwOk ? XIAOHEIHE_KEYWORD_MONITOR.kind : XIAOHEIHE_ACCOUNT_MONITOR.kind))
  }
}, { immediate: true })

async function create() {
  const { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText, includePrerelease, ...rest } = form
  const ok = await center.createCustom({
    ...rest,
    platform: activeKindMeta.value.platform,
    name: rest.name.trim(),
    target_value: isHotPosts.value ? XIAOHEIHE_MONITOR.target : rest.target_value.trim(),
    interval_minutes: isHotPosts.value ? undefined : rest.interval_minutes,
    min_views: isKeywordKind(rest.kind) ? Number(rest.min_views) || 0 : 0,
    filters: isHotPosts.value ? {} : buildFilters(rest.kind, { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText, includePrerelease }),
  })
  if (ok) {
    Object.assign(form, blank(), { interval_minutes: customQuota.value.min_interval_minutes })
    webPreview.value = null
    webPreviewError.value = ''
    advancedOpen.value = false
    creating.value = false
  }
}
function startEdit(row: CustomFeedRow) {
  openMenuId.value = null
  editing.value = row.id
  Object.assign(draft, {
    name: row.name, target_value: row.target_value, min_views: row.min_views, interval_minutes: row.interval_minutes,
    includeText: (row.filters?.include ?? []).join('，'), excludeText: (row.filters?.exclude ?? []).join('，'),
    selector: row.filters?.selector ?? '',
    excludeSelector: row.filters?.exclude_selector ?? '',
    linkPattern: row.filters?.link_pattern ?? '',
    ignoreText: (row.filters?.ignore_patterns ?? []).join('，'),
    includePrerelease: row.filters?.include_prerelease !== false,
  })
  editAdvancedOpen.value = Boolean(
    row.min_views
    || row.filters?.include?.length
    || row.filters?.exclude?.length
    || row.filters?.selector
    || row.filters?.exclude_selector
    || row.filters?.link_pattern
    || row.filters?.ignore_patterns?.length
    || row.filters?.include_prerelease === false,
  )
  customError.value = customMessage.value = ''
}
async function saveEdit(row: CustomFeedRow) {
  const { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText, includePrerelease, ...rest } = draft
  const changes: Record<string, unknown> = { name: rest.name.trim() }
  if (row.kind !== XIAOHEIHE_MONITOR.kind) {
    changes.target_value = rest.target_value.trim()
    changes.interval_minutes = rest.interval_minutes
    changes.min_views = isKeywordKind(row.kind) ? rest.min_views : 0
    changes.filters = buildFilters(row.kind, { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText, includePrerelease })
  }
  const ok = await center.patchCustom(row, changes, '监控配置已保存')
  if (ok) editing.value = null
}
function remove(row: CustomFeedRow) {
  openMenuId.value = null
  if (confirm(`确认删除监控「${row.name}」及其全部采集记录？`)) {
    center.removeCustom(row).then(() => { if (editing.value === row.id) editing.value = null })
  }
}
function patchFromMenu(row: CustomFeedRow, body: Record<string, unknown>, okText: string) {
  openMenuId.value = null
  void center.patchCustom(row, body, okText)
}
function statusOf(row: CustomFeedRow) {
  if (row.status !== 'active') return { label: '已停用', dot: 'bg-hot' }
  if (!row.enabled) return { label: '已暂停', dot: 'bg-faint' }
  if (row.kind === XIAOHEIHE_MONITOR.kind) return row.last_error ? { label: '更新暂缓', dot: 'bg-warn' } : { label: '已关注', dot: 'bg-ok' }
  return row.last_error ? { label: '采集异常', dot: 'bg-hot' } : { label: '运行中', dot: 'bg-ok' }
}
function lastCollected(row: CustomFeedRow) {
  const ts = toTimestamp(row.last_collected_at)
  return Number.isFinite(ts) ? `${relativeTime(ts, now.value)}采集` : '等待首次采集'
}
const primaryAction = 'inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[13px] text-ink-2 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40'
const menuItem = 'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-ink-2 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40'
</script>

<template>
  <div id="custom-monitors" class="scroll-mt-24 space-y-5">
    <section class="panel p-5" aria-labelledby="monitors-title">
      <header class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 id="monitors-title" class="text-base font-bold text-ink">我的监控</h2>
          <p class="mt-1 text-xs leading-relaxed text-muted">
            支持 RSS 订阅、网页监测、GitHub、B 站、YouTube、Steam 与小黑盒。公开后将展示在<NuxtLink to="/custom" class="link">定制监控频道</NuxtLink>，私有仅自己可见。
          </p>
        </div>
        <button v-if="!creating" type="button" class="btn-primary inline-flex h-9 items-center gap-1 px-3.5" :disabled="!canCreate || loading.custom" @click="openCreate">
          <Plus :size="15" /> 新建监控
        </button>
      </header>

      <div class="mt-4">
        <div class="flex items-baseline justify-between text-xs text-muted">
          <span>已用 <span class="num text-sm font-bold text-ink">{{ customQuota.used }}</span> / {{ customQuota.max_feeds }} 项</span>
          <span>最短采集间隔 {{ customQuota.min_interval_minutes }} 分钟</span>
        </div>
        <div class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-chip">
          <div class="h-full rounded-full transition-[width]" :class="quotaFull ? 'bg-warn' : 'bg-brand'" :style="{ width: `${quotaPercent}%` }" />
        </div>
      </div>
      <UiNotice v-if="!loading.custom && !errors.custom && !customQuota.admitted" tone="warn" class="mt-4">当前账号尚未开通定制监控权限，请联系管理员</UiNotice>

      <form v-if="creating" class="mt-5 space-y-4 rounded-xl border border-line bg-canvas/60 p-4" @submit.prevent="create">
        <!-- 分组选源 -->
        <div class="space-y-2.5" role="radiogroup" aria-label="监控类型">
          <div v-for="group in groupedKinds" :key="group.key" class="flex flex-wrap items-center gap-1.5">
            <span class="w-20 shrink-0 text-xs font-medium text-faint">{{ group.label }}</span>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="kind in group.items"
                :key="kind.key"
                type="button"
                role="radio"
                :aria-checked="form.kind === kind.key"
                class="h-8 rounded-lg border px-3 text-xs transition-colors sm:text-[13px]"
                :class="form.kind === kind.key
                  ? 'border-brand bg-brand-soft font-semibold text-brand-ink'
                  : 'border-line bg-card text-ink-2 hover:border-faint/60 hover:text-ink'"
                @click="selectKind(kind.key)"
              >
                {{ kind.label }}
              </button>
            </div>
          </div>
          <p class="border-t border-line-soft pt-2 text-xs text-muted">
            <span class="font-semibold text-ink-2">{{ activeKindMeta.label }}</span>
            <span class="mx-1.5 text-faint">·</span>
            {{ activeKindMeta.desc }}
          </p>
        </div>

        <!-- 主路径必填字段 -->
        <div class="grid gap-3 sm:grid-cols-2">
          <div v-if="!isHotPosts" :class="isWebKind(form.kind) ? 'sm:col-span-2' : ''">
            <label class="block text-xs font-medium text-ink-2">{{ activeKindMeta.fieldLabel }}</label>
            <div class="mt-1.5 flex gap-2">
              <input
                v-model="form.target_value"
                required
                :maxlength="isUrlKind(form.kind) ? 255 : 128"
                :type="isUrlKind(form.kind) ? 'url' : 'text'"
                :inputmode="form.kind === 'uid' ? 'numeric' : undefined"
                :pattern="form.kind === 'uid' ? '[0-9]+' : undefined"
                :placeholder="activeKindMeta.placeholder"
                class="input h-10 min-w-0 flex-1 font-normal"
              >
              <button
                v-if="isWebKind(form.kind)"
                type="button"
                class="h-10 shrink-0 rounded-[10px] border border-line bg-card px-3.5 text-xs font-medium text-brand-ink transition-colors hover:bg-chip disabled:opacity-50"
                :disabled="webPreviewing || !form.target_value.trim()"
                @click="previewWeb"
              >
                {{ webPreviewing ? '解析中…' : '测试解析' }}
              </button>
            </div>
          </div>

          <label class="text-xs font-medium text-ink-2" :class="isHotPosts || isWebKind(form.kind) ? 'sm:col-span-2' : ''">监控名称
            <input v-model="form.name" required maxlength="64" placeholder="例如：独立游戏资讯 / 官方更新日志" class="input mt-1.5 h-10 w-full font-normal">
          </label>
        </div>

        <!-- 网页测试解析结果 -->
        <template v-if="isWebKind(form.kind)">
          <p v-if="webPreviewError" class="text-xs text-hot">{{ webPreviewError }}</p>
          <div v-if="webPreview" class="space-y-2 rounded-xl border border-line bg-card p-3 text-xs">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-ink">{{ webPreview.title }}</span>
              <span class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted">{{ webExtractorLabel(webPreview.extractor) }}</span>
              <span v-if="webPreview.mode === 'page_watch'" class="text-muted">正文 {{ webPreview.text_length }} 字</span>
              <span v-else class="text-muted">识别 {{ webPreview.items.length }} 条链接</span>
            </div>
            <p v-if="webPreview.spa_warning" class="rounded bg-amber-500/10 px-2.5 py-1.5 text-amber-700 dark:text-amber-300">
              该页面可能为前端脚本渲染，静态解析内容较少，建议优先改用 RSS 订阅
            </p>
            <div v-if="webPreview.detected_feeds.length" class="flex flex-wrap items-center gap-1.5 rounded bg-emerald-500/10 px-2.5 py-1.5 text-emerald-700 dark:text-emerald-300">
              <span>检测到官方 RSS：</span>
              <button
                v-for="f in webPreview.detected_feeds"
                :key="f.url"
                type="button"
                class="rounded border border-emerald-500/40 bg-card px-2 py-0.5 font-medium hover:bg-emerald-500/10"
                @click="switchToDetectedFeed(f.url, f.title)"
              >
                改用 {{ f.title || 'RSS 订阅' }}
              </button>
            </div>
            <pre v-if="webPreview.mode === 'page_watch'" class="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-chip/60 p-2 font-sans leading-relaxed text-ink-2">{{ webPreview.text_preview || '（未提取到正文）' }}</pre>
            <ul v-else-if="webPreview.items.length" class="max-h-36 space-y-1 overflow-auto">
              <li v-for="(item, idx) in webPreview.items" :key="idx" class="truncate text-ink-2">· {{ item.title }}</li>
            </ul>
            <p v-else class="text-muted">未识别到文章链接，可在下方高级选项中填写内容区域或链接规则后重试</p>
          </div>
        </template>

        <!-- 折叠高级选项 -->
        <div v-if="!isHotPosts" class="border-t border-line-soft pt-3">
          <button
            type="button"
            class="inline-flex items-center gap-1.5 text-xs font-medium text-muted transition-colors hover:text-ink"
            :aria-expanded="advancedOpen"
            @click="advancedOpen = !advancedOpen"
          >
            <ChevronDown :size="14" class="transition-transform" :class="{ 'rotate-180': advancedOpen }" />
            高级选项
            <span v-if="advancedConfiguredCount" class="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] text-brand-ink">已设 {{ advancedConfiguredCount }} 项</span>
          </button>

          <div v-if="advancedOpen" class="mt-3 grid gap-3 sm:grid-cols-2">
            <label class="text-xs font-medium text-ink-2">采集间隔（分钟）
              <input v-model.number="form.interval_minutes" type="number" required :min="customQuota.min_interval_minutes" max="1440" class="input mt-1.5 h-9 w-full font-normal">
            </label>
            <label v-if="isKeywordKind(form.kind)" class="text-xs font-medium text-ink-2">{{ form.kind === XIAOHEIHE_KEYWORD_MONITOR.kind ? '最低阅读量' : '最低播放量' }}
              <input v-model.number="form.min_views" type="number" min="0" max="10000000" step="1" class="input mt-1.5 h-9 w-full font-normal">
            </label>
            <label class="text-xs font-medium text-ink-2">标题包含词
              <input v-model="form.includeText" placeholder="逗号分隔，命中任一即收录" class="input mt-1.5 h-9 w-full font-normal">
            </label>
            <label class="text-xs font-medium text-ink-2">标题排除词
              <input v-model="form.excludeText" placeholder="逗号分隔，命中任一即过滤" class="input mt-1.5 h-9 w-full font-normal">
            </label>
            <template v-if="isWebKind(form.kind)">
              <label class="text-xs font-medium text-ink-2">内容区域
                <input v-model="form.selector" maxlength="200" placeholder="留空自动提取正文，支持 CSS / XPath（如 main、#pricing）" class="input mono mt-1.5 h-9 w-full text-xs font-normal">
              </label>
              <label class="text-xs font-medium text-ink-2">排除区域
                <input v-model="form.excludeSelector" maxlength="200" placeholder="如 .ads, footer, nav" class="input mono mt-1.5 h-9 w-full text-xs font-normal">
              </label>
              <label v-if="form.kind === WEB_LIST_MONITOR.kind" class="text-xs font-medium text-ink-2 sm:col-span-2">链接规则
                <input v-model="form.linkPattern" maxlength="200" placeholder="仅收录匹配路径，如 /blog/ 或 regex:/news/2026" class="input mono mt-1.5 h-9 w-full text-xs font-normal">
              </label>
              <label v-if="form.kind === WEB_PAGE_MONITOR.kind" class="text-xs font-medium text-ink-2 sm:col-span-2">忽略内容
                <input v-model="form.ignoreText" placeholder="逗号分隔，如 在线人数, 浏览量" class="input mono mt-1.5 h-9 w-full text-xs font-normal">
              </label>
            </template>
            <label v-if="isGitReleaseKind(form.kind)" class="flex cursor-pointer items-center gap-2 text-xs text-ink-2 sm:col-span-2">
              <input v-model="form.includePrerelease" type="checkbox" class="checkbox">
              <span>包含预发布版本（alpha / beta / rc）</span>
            </label>
          </div>
        </div>

        <div class="flex flex-wrap gap-x-5 gap-y-2">
          <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input v-model="form.is_public" type="checkbox" class="checkbox"> 公开到定制监控频道</label>
          <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input v-model="form.push_enabled" type="checkbox" class="checkbox"> 新内容邮件推送</label>
        </div>
        <p v-if="form.push_enabled" class="text-xs text-faint">需在消息通知中开启邮件通知，单项监控每日最多发送一封摘要</p>
        <p v-if="isHotPosts && existingXiaoheihe" class="text-sm text-muted">已关注小黑盒热帖。<NuxtLink :to="`/custom/${existingXiaoheihe.id}`" class="link">查看监控</NuxtLink></p>
        <div class="flex gap-2">
          <button type="submit" class="btn-primary h-9 px-4" :disabled="customBusy || !canCreate || (isHotPosts && Boolean(existingXiaoheihe))">{{ customBusy ? '创建中…' : isHotPosts ? '关注热帖' : '创建监控' }}</button>
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
      <p class="text-sm text-muted">暂无定制监控</p>
      <p class="mt-1 text-xs text-faint">{{ customQuota.admitted ? '点击「新建监控」添加 RSS 订阅、网页监测或社区账号' : '开通使用权限后即可创建' }}</p>
    </div>
    <ul v-else class="grid gap-4 md:grid-cols-2">
      <li v-for="row in customFeeds" :key="row.id" class="panel flex flex-col p-4">
        <template v-if="editing !== row.id">
          <div class="flex items-center gap-2 text-xs">
            <span class="rounded bg-chip px-1.5 py-0.5 font-medium text-ink-2">{{ customKindLabel(row.kind) }}</span>
            <span class="rounded px-1.5 py-0.5 font-medium" :class="row.is_public ? 'bg-brand-soft text-brand-ink' : 'bg-chip text-muted'">{{ row.is_public ? '公开' : '私有' }}</span>
            <span v-if="row.push_enabled" class="rounded bg-chip px-1.5 py-0.5 text-muted">邮件推送</span>
            <span class="ml-auto flex items-center gap-1.5 text-muted">
              <span class="size-1.5 rounded-full" :class="statusOf(row).dot" aria-hidden="true" />{{ statusOf(row).label }}
            </span>
          </div>
          <NuxtLink :to="`/custom/${row.id}`" class="mt-2.5 line-clamp-1 text-base font-bold text-ink hover:text-brand-ink">{{ row.name }}</NuxtLink>
          <p v-if="row.kind !== XIAOHEIHE_MONITOR.kind" class="mono mt-0.5 truncate text-xs text-faint">{{ row.kind === 'uid' || row.kind === XIAOHEIHE_ACCOUNT_MONITOR.kind ? `UID ${row.target_value}` : row.target_value }}</p>
          <p class="mt-3 flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
            <span><span class="num text-[15px] font-bold text-ink">{{ row.item_count }}</span> 条</span>
            <span v-if="row.kind !== XIAOHEIHE_MONITOR.kind">· 每 {{ row.interval_minutes }} 分钟</span>
            <span>· {{ lastCollected(row) }}</span>
          </p>
          <p v-if="row.enabled && row.last_error" class="mt-1 text-xs text-hot">上次采集异常：{{ row.last_error }}</p>
          <p v-if="row.min_views || row.filters?.include?.length || row.filters?.exclude?.length || row.filters?.selector || row.filters?.link_pattern || row.filters?.include_prerelease === false" class="mt-1 text-xs text-faint">
            <template v-if="row.min_views">{{ row.kind === XIAOHEIHE_KEYWORD_MONITOR.kind ? '阅读' : '播放' }} ≥ {{ row.min_views.toLocaleString('zh-CN') }}　</template>
            <template v-if="row.filters?.include_prerelease === false">仅正式版　</template>
            <template v-if="row.filters?.selector">内容区域：{{ row.filters.selector }}　</template>
            <template v-if="row.filters?.link_pattern">链接规则：{{ row.filters.link_pattern }}　</template>
            <template v-if="row.filters?.include?.length">包含：{{ row.filters.include.join('、') }}　</template>
            <template v-if="row.filters?.exclude?.length">排除：{{ row.filters.exclude.join('、') }}</template>
          </p>
          <div class="min-h-3 flex-1" aria-hidden="true" />
          <div class="flex items-center gap-1 border-t border-line-soft pt-2.5">
            <NuxtLink :to="`/custom/${row.id}`" :class="primaryAction">
              <ExternalLink :size="13" /> 查看内容
            </NuxtLink>
            <button
              v-if="row.kind !== XIAOHEIHE_MONITOR.kind"
              type="button"
              :class="primaryAction"
              :disabled="customBusy || row.status !== 'active' || !row.enabled"
              :title="row.enabled ? '立即加入采集队列' : '恢复监控后可手动采集'"
              @click="center.triggerCustom(row)"
            >
              <RefreshCw :size="13" /> 立即采集
            </button>
            <div class="relative ml-auto" data-monitor-menu>
              <button
                type="button"
                class="grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-chip hover:text-ink"
                :aria-expanded="openMenuId === row.id"
                aria-haspopup="menu"
                :aria-label="`更多操作：${row.name}`"
                @click="openMenuId = openMenuId === row.id ? null : row.id"
              >
                <MoreHorizontal :size="16" />
              </button>
              <div
                v-if="openMenuId === row.id"
                role="menu"
                class="popover absolute bottom-full right-0 z-30 mb-1 w-40 p-1"
              >
                <button type="button" role="menuitem" :class="menuItem" :disabled="customBusy" @click="startEdit(row)">
                  <Pencil :size="13" /> 编辑配置
                </button>
                <button
                  type="button"
                  role="menuitem"
                  :class="menuItem"
                  :disabled="customBusy"
                  @click="patchFromMenu(row, { is_public: !row.is_public }, row.is_public ? '已设为私有' : '已公开到定制监控频道')"
                >
                  <EyeOff v-if="row.is_public" :size="13" /><Eye v-else :size="13" />{{ row.is_public ? '设为私有' : '公开到频道' }}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  :class="menuItem"
                  :disabled="customBusy"
                  @click="patchFromMenu(row, { push_enabled: !row.push_enabled }, row.push_enabled ? '已关闭邮件推送' : '已开启邮件推送')"
                >
                  <MailX v-if="row.push_enabled" :size="13" /><Mail v-else :size="13" />{{ row.push_enabled ? '关闭邮件推送' : '开启邮件推送' }}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  :class="menuItem"
                  :disabled="customBusy || row.status !== 'active'"
                  @click="patchFromMenu(row, { enabled: !row.enabled }, row.enabled ? '监控已暂停' : '监控已恢复')"
                >
                  <Pause v-if="row.enabled" :size="13" /><Play v-else :size="13" />{{ row.enabled ? '暂停监控' : '恢复监控' }}
                </button>
                <div class="my-1 border-t border-line-soft" />
                <button
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-hot transition-colors hover:bg-hot/10 disabled:opacity-40"
                  :disabled="customBusy"
                  @click="remove(row)"
                >
                  <Trash2 :size="13" /> 删除监控
                </button>
              </div>
            </div>
          </div>
        </template>

        <form v-else class="grid gap-3" @submit.prevent="saveEdit(row)">
          <p class="text-sm font-bold text-ink">编辑监控</p>
          <label class="text-xs font-medium text-ink-2">监控名称<input v-model="draft.name" required maxlength="64" class="input mt-1.5 h-9 w-full font-normal"></label>
          <template v-if="row.kind !== XIAOHEIHE_MONITOR.kind">
            <label class="text-xs font-medium text-ink-2">{{ kindFieldLabel(row.kind) }}<input v-model="draft.target_value" required :maxlength="isUrlKind(row.kind) ? 255 : 128" :type="isUrlKind(row.kind) ? 'url' : 'text'" :pattern="row.kind === 'uid' ? '[0-9]+' : undefined" class="input mt-1.5 h-9 w-full font-normal"></label>
            <div>
              <button
                type="button"
                class="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-ink"
                :aria-expanded="editAdvancedOpen"
                @click="editAdvancedOpen = !editAdvancedOpen"
              >
                <ChevronDown :size="13" class="transition-transform" :class="{ 'rotate-180': editAdvancedOpen }" /> 高级选项
              </button>
              <div v-if="editAdvancedOpen" class="mt-2.5 grid gap-2.5">
                <div class="grid grid-cols-2 gap-3">
                  <label class="text-xs font-medium text-ink-2">采集间隔（分钟）<input v-model.number="draft.interval_minutes" type="number" required :min="Math.min(row.interval_minutes, customQuota.min_interval_minutes)" max="1440" class="input mt-1.5 h-9 w-full font-normal"></label>
                  <label v-if="isKeywordKind(row.kind)" class="text-xs font-medium text-ink-2">{{ row.kind === XIAOHEIHE_KEYWORD_MONITOR.kind ? '最低阅读量' : '最低播放量' }}<input v-model.number="draft.min_views" type="number" required min="0" max="10000000" class="input mt-1.5 h-9 w-full font-normal"></label>
                </div>
                <label class="text-xs font-medium text-ink-2">标题包含词<input v-model="draft.includeText" placeholder="逗号分隔" class="input mt-1.5 h-9 w-full font-normal"></label>
                <label class="text-xs font-medium text-ink-2">标题排除词<input v-model="draft.excludeText" placeholder="逗号分隔" class="input mt-1.5 h-9 w-full font-normal"></label>
                <template v-if="isWebKind(row.kind)">
                  <label class="text-xs font-medium text-ink-2">内容区域<input v-model="draft.selector" maxlength="200" placeholder="CSS / XPath，留空自动提取" class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
                  <label class="text-xs font-medium text-ink-2">排除区域<input v-model="draft.excludeSelector" maxlength="200" placeholder="如 .ads, footer" class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
                  <label v-if="row.kind === WEB_LIST_MONITOR.kind" class="text-xs font-medium text-ink-2">链接规则<input v-model="draft.linkPattern" maxlength="200" placeholder="/blog/ 或 regex:..." class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
                  <label v-if="row.kind === WEB_PAGE_MONITOR.kind" class="text-xs font-medium text-ink-2">忽略内容<input v-model="draft.ignoreText" placeholder="逗号分隔" class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
                </template>
                <label v-if="isGitReleaseKind(row.kind)" class="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
                  <input v-model="draft.includePrerelease" type="checkbox" class="checkbox">
                  <span>包含预发布版本（alpha / beta / rc）</span>
                </label>
              </div>
            </div>
            <p class="text-xs text-faint">修改目标地址将保留已采集内容，后续按新目标继续采集</p>
          </template>
          <div class="flex gap-2">
            <button type="submit" class="btn-primary h-9 px-4" :disabled="customBusy">{{ customBusy ? '保存中…' : '保存更改' }}</button>
            <button type="button" class="h-9 rounded-[10px] px-3 text-sm text-muted hover:bg-chip hover:text-ink" @click="editing = null">取消</button>
          </div>
        </form>
      </li>
    </ul>
  </div>
</template>
