<script setup lang="ts">
import { Eye, EyeOff, Mail, MailX, Pause, Pencil, Play, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import type { CustomFeedRow } from '~/composables/useAccountCenter'
import { apiErrorDetail } from '~/utils/api-errors'
import {
  customKindLabel,
  STEAM_MONITOR,
  WEB_LIST_MONITOR,
  WEB_PAGE_MONITOR,
  XIAOHEIHE_ACCOUNT_MONITOR,
  XIAOHEIHE_KEYWORD_MONITOR,
  XIAOHEIHE_MONITOR,
} from '~/utils/custom-feeds'
import { relativeTime, toTimestamp } from '~/utils/schedule'

// 定制监控：自建 B 站关键词 / UP 主、RSS/Atom 订阅、自定义网页变更/列表、YouTube 频道、Steam 游戏、小黑盒账号 / 关键词抓取源。公开的会出现在"定制监控"频道，私有的仅自己可见。
const route = useRoute()
const center = useAccountCenter()
const { customFeeds, customQuota, customBusy, customError, customMessage, loading, errors } = center
const now = ref(Date.now())
const creating = ref(route.query.create === '1')

const ALL_KINDS = [
  { key: 'keyword', platform: 'bilibili', label: 'B 站关键词', fieldLabel: '搜索关键词', placeholder: '例如：独立游戏 发售、机械键盘 评测' },
  { key: 'uid', platform: 'bilibili', label: 'B 站 UP 主', fieldLabel: 'UP 主 UID', placeholder: '纯数字，空间主页地址里的那串数字' },
  { key: 'feed', platform: 'rss', label: 'RSS / Atom', fieldLabel: 'RSS / Atom 地址', placeholder: 'https://example.com/feed.xml' },
  { key: WEB_PAGE_MONITOR.kind, platform: WEB_PAGE_MONITOR.platform, label: WEB_PAGE_MONITOR.name, fieldLabel: '目标网页地址', placeholder: 'https://example.com/changelog（跟踪页面正文变更）' },
  { key: WEB_LIST_MONITOR.kind, platform: WEB_LIST_MONITOR.platform, label: WEB_LIST_MONITOR.name, fieldLabel: '列表页地址', placeholder: 'https://example.com/news（自动发现新增文章）' },
  { key: 'channel_rss', platform: 'youtube', label: 'YouTube 频道', fieldLabel: '频道链接、@handle 或频道 ID', placeholder: 'https://www.youtube.com/@xxx、@xxx 或 UC...' },
  { key: STEAM_MONITOR.kind, platform: STEAM_MONITOR.platform, label: STEAM_MONITOR.name, fieldLabel: 'Steam AppID 或商店链接', placeholder: '例如 3513350 或 https://store.steampowered.com/app/3513350/' },
  { key: XIAOHEIHE_ACCOUNT_MONITOR.kind, platform: XIAOHEIHE_ACCOUNT_MONITOR.platform, label: XIAOHEIHE_ACCOUNT_MONITOR.name, fieldLabel: '小黑盒 UID 或个人主页链接', placeholder: '例如 12345678 或 https://xiaoheihe.cn/app/user/profile/12345678' },
  { key: XIAOHEIHE_KEYWORD_MONITOR.kind, platform: XIAOHEIHE_KEYWORD_MONITOR.platform, label: XIAOHEIHE_KEYWORD_MONITOR.name, fieldLabel: '小黑盒搜索关键词', placeholder: '例如：兑换码、联名键盘、鸣潮' },
  { key: XIAOHEIHE_MONITOR.kind, platform: XIAOHEIHE_MONITOR.platform, label: XIAOHEIHE_MONITOR.name, fieldLabel: '', placeholder: '' },
] as const
type MonitorKind = typeof ALL_KINDS[number]['key']

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

const KINDS = computed(() => ALL_KINDS.filter((k) => {
  if (k.key === XIAOHEIHE_KEYWORD_MONITOR.kind) return Boolean(customQuota.value.xiaoheihe_keyword_enabled)
  if (k.key === XIAOHEIHE_MONITOR.kind) return Boolean(customQuota.value.xiaoheihe_hot_enabled)
  return true
}))

const blank = () => ({
  name: '', kind: 'keyword' as MonitorKind, target_value: '', min_views: 0, interval_minutes: 60,
  includeText: '', excludeText: '', selector: '', excludeSelector: '', linkPattern: '', ignoreText: '',
  push_enabled: false, is_public: false,
})
const form = reactive(blank())
const editing = ref<number | null>(null)
const draft = reactive({
  name: '', target_value: '', min_views: 0, interval_minutes: 30,
  includeText: '', excludeText: '', selector: '', excludeSelector: '', linkPattern: '', ignoreText: '',
})
const webPreview = ref<WebPreviewResult | null>(null)
const webPreviewing = ref(false)
const webPreviewError = ref('')

const activeKindMeta = computed(() => ALL_KINDS.find(k => k.key === form.kind) ?? ALL_KINDS[0])
const isHotPosts = computed(() => form.kind === XIAOHEIHE_MONITOR.kind)
const isKeywordKind = (kind: string) => kind === 'keyword' || kind === XIAOHEIHE_KEYWORD_MONITOR.kind
const isWebKind = (kind: string) => kind === WEB_PAGE_MONITOR.kind || kind === WEB_LIST_MONITOR.kind
const isUrlKind = (kind: string) => kind === 'feed' || isWebKind(kind)
const existingXiaoheihe = computed(() => customFeeds.value.find(row => row.kind === XIAOHEIHE_MONITOR.kind))
const kindFieldLabel = (kind: string) => ALL_KINDS.find(k => k.key === kind)?.fieldLabel ?? '监控目标'

const words = (value: string) => value.split(/[,，\n]+/).map(v => v.trim()).filter(Boolean)
const quotaFull = computed(() => customQuota.value.used >= customQuota.value.max_feeds)
const canCreate = computed(() => customQuota.value.admitted && !quotaFull.value)
const quotaPercent = computed(() => Math.min(100, (customQuota.value.used / Math.max(1, customQuota.value.max_feeds)) * 100))

watch(() => customQuota.value.min_interval_minutes, (min) => { form.interval_minutes = Math.max(form.interval_minutes, min) }, { immediate: true })

function openCreate() {
  creating.value = true
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
function buildFilters(kind: string, src: { includeText: string, excludeText: string, selector: string, excludeSelector: string, linkPattern: string, ignoreText: string }) {
  const filters: Record<string, unknown> = { include: words(src.includeText), exclude: words(src.excludeText) }
  if (isWebKind(kind)) {
    if (src.selector.trim()) filters.selector = src.selector.trim()
    if (src.excludeSelector.trim()) filters.exclude_selector = src.excludeSelector.trim()
    if (kind === WEB_LIST_MONITOR.kind && src.linkPattern.trim()) filters.link_pattern = src.linkPattern.trim()
    if (kind === WEB_PAGE_MONITOR.kind && words(src.ignoreText).length) filters.ignore_patterns = words(src.ignoreText)
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
    webPreviewError.value = apiErrorDetail(exc, '网页解析测试失败')
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
  if (route.query.source === STEAM_MONITOR.platform && form.kind === 'keyword') {
    selectKind(STEAM_MONITOR.kind)
  } else if (route.query.source === XIAOHEIHE_MONITOR.platform && form.kind === 'keyword') {
    selectKind(hotOk ? XIAOHEIHE_MONITOR.kind : (kwOk ? XIAOHEIHE_KEYWORD_MONITOR.kind : XIAOHEIHE_ACCOUNT_MONITOR.kind))
  }
}, { immediate: true })

async function create() {
  const { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText, ...rest } = form
  const ok = await center.createCustom({
    ...rest,
    platform: activeKindMeta.value.platform,
    name: rest.name.trim(),
    target_value: isHotPosts.value ? XIAOHEIHE_MONITOR.target : rest.target_value.trim(),
    interval_minutes: isHotPosts.value ? undefined : rest.interval_minutes,
    min_views: isKeywordKind(rest.kind) ? Number(rest.min_views) || 0 : 0,
    filters: isHotPosts.value ? {} : buildFilters(rest.kind, { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText }),
  })
  if (ok) {
    Object.assign(form, blank(), { interval_minutes: customQuota.value.min_interval_minutes })
    webPreview.value = null
    webPreviewError.value = ''
    creating.value = false
  }
}
function startEdit(row: CustomFeedRow) {
  editing.value = row.id
  Object.assign(draft, {
    name: row.name, target_value: row.target_value, min_views: row.min_views, interval_minutes: row.interval_minutes,
    includeText: (row.filters?.include ?? []).join('，'), excludeText: (row.filters?.exclude ?? []).join('，'),
    selector: row.filters?.selector ?? '',
    excludeSelector: row.filters?.exclude_selector ?? '',
    linkPattern: row.filters?.link_pattern ?? '',
    ignoreText: (row.filters?.ignore_patterns ?? []).join('，'),
  })
  customError.value = customMessage.value = ''
}
async function saveEdit(row: CustomFeedRow) {
  const { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText, ...rest } = draft
  const changes: Record<string, unknown> = { name: rest.name.trim() }
  if (row.kind !== XIAOHEIHE_MONITOR.kind) {
    changes.target_value = rest.target_value.trim()
    changes.interval_minutes = rest.interval_minutes
    changes.min_views = isKeywordKind(row.kind) ? rest.min_views : 0
    changes.filters = buildFilters(row.kind, { includeText, excludeText, selector, excludeSelector, linkPattern, ignoreText })
  }
  const ok = await center.patchCustom(row, changes, '监控已更新。')
  if (ok) editing.value = null
}
function remove(row: CustomFeedRow) {
  if (confirm(`删除监控「${row.name}」及其全部抓取记录？`)) center.removeCustom(row).then(() => { if (editing.value === row.id) editing.value = null })
}
function statusOf(row: CustomFeedRow) {
  if (row.status !== 'active') return { label: '管理员已停用', dot: 'bg-hot' }
  if (!row.enabled) return { label: '已暂停', dot: 'bg-faint' }
  if (row.kind === XIAOHEIHE_MONITOR.kind) return row.last_error ? { label: '更新暂缓', dot: 'bg-warn' } : { label: '已关注', dot: 'bg-ok' }
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
            选择 B 站、RSS / Atom、自定义网页、YouTube、Steam 游戏或小黑盒。公开的监控会展示在<NuxtLink to="/custom" class="link">定制监控频道</NuxtLink>，私有的仅自己可见。
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
        <div class="flex flex-wrap gap-1 rounded-xl bg-chip p-1 text-xs sm:text-sm" role="radiogroup" aria-label="监控类型">
          <button
            v-for="kind in KINDS"
            :key="kind.key"
            type="button"
            role="radio"
            :aria-checked="form.kind === kind.key"
            class="h-8 min-w-[6.2rem] flex-1 rounded-[9px] px-2 transition-colors"
            :class="form.kind === kind.key ? 'bg-card font-semibold text-ink shadow-card' : 'text-muted hover:text-ink'"
            @click="selectKind(kind.key)"
          >
            {{ kind.label }}
          </button>
        </div>
        <div class="mt-4 grid gap-3 sm:grid-cols-2">
          <label v-if="!isHotPosts" class="text-xs font-medium text-ink-2">{{ activeKindMeta.fieldLabel }}
            <input
              v-model="form.target_value"
              required
              :maxlength="isUrlKind(form.kind) ? 255 : 128"
              :type="isUrlKind(form.kind) ? 'url' : 'text'"
              :inputmode="form.kind === 'uid' ? 'numeric' : undefined"
              :pattern="form.kind === 'uid' ? '[0-9]+' : undefined"
              :placeholder="activeKindMeta.placeholder"
              class="input mt-1.5 h-10 w-full font-normal"
            >
          </label>
          <label class="text-xs font-medium text-ink-2">监控名称
            <input v-model="form.name" required maxlength="64" placeholder="例如：独立游戏资讯 / 官网更新日志" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <template v-if="isWebKind(form.kind)">
            <label class="text-xs font-medium text-ink-2">内容区域选择器 <span class="font-normal text-faint">（CSS/XPath，留空自动提取）</span>
              <input v-model="form.selector" maxlength="200" placeholder="例如：main、#changelog、article" class="input mono mt-1.5 h-10 w-full text-xs font-normal">
            </label>
            <label class="text-xs font-medium text-ink-2">排除噪音区域 <span class="font-normal text-faint">（可选）</span>
              <input v-model="form.excludeSelector" maxlength="200" placeholder="例如：.ads, footer, .timestamp" class="input mono mt-1.5 h-10 w-full text-xs font-normal">
            </label>
            <label v-if="form.kind === WEB_LIST_MONITOR.kind" class="text-xs font-medium text-ink-2 sm:col-span-2">文章链接匹配规则 <span class="font-normal text-faint">（可选，路径前缀或 regex:）</span>
              <input v-model="form.linkPattern" maxlength="200" placeholder="例如：/blog/ 或 regex:/news/2026" class="input mono mt-1.5 h-10 w-full text-xs font-normal">
            </label>
            <label v-if="form.kind === WEB_PAGE_MONITOR.kind" class="text-xs font-medium text-ink-2 sm:col-span-2">忽略动态行 <span class="font-normal text-faint">（逗号分隔，防浏览量等噪音误报，可选）</span>
              <input v-model="form.ignoreText" placeholder="例如：在线人数, regex:更新于\\s+\\d+分钟前" class="input mono mt-1.5 h-10 w-full text-xs font-normal">
            </label>
            <div class="sm:col-span-2 space-y-2">
              <button
                type="button"
                class="inline-flex h-8 items-center rounded-lg border border-line bg-card px-3 text-xs font-medium text-brand-ink hover:bg-chip disabled:opacity-50"
                :disabled="webPreviewing || !form.target_value.trim()"
                @click="previewWeb"
              >
                {{ webPreviewing ? '正在解析…' : '测试解析效果' }}
              </button>
              <p v-if="webPreviewError" class="text-xs text-hot">{{ webPreviewError }}</p>
              <div v-if="webPreview" class="space-y-2 rounded-xl border border-line bg-card p-3 text-xs">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-semibold text-ink">{{ webPreview.title }}</span>
                  <span class="rounded bg-chip px-1.5 py-0.5 mono text-[11px] text-muted">{{ webPreview.extractor }}</span>
                  <span v-if="webPreview.mode === 'page_watch'" class="text-muted">提取 {{ webPreview.text_length }} 字</span>
                  <span v-else class="text-muted">发现 {{ webPreview.items.length }} 条链接</span>
                </div>
                <p v-if="webPreview.spa_warning" class="rounded bg-amber-500/10 px-2.5 py-1.5 text-amber-700 dark:text-amber-300">
                  该网页可能是前端 JS 渲染页，静态抓取内容较少，建议优先改用 RSS 订阅。
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
                    改用 {{ f.title || 'RSS' }}
                  </button>
                </div>
                <pre v-if="webPreview.mode === 'page_watch'" class="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-chip/60 p-2 font-sans leading-relaxed text-ink-2">{{ webPreview.text_preview || '（未提取到正文）' }}</pre>
                <ul v-else-if="webPreview.items.length" class="max-h-36 space-y-1 overflow-auto">
                  <li v-for="(item, idx) in webPreview.items" :key="idx" class="truncate text-ink-2">· {{ item.title }}</li>
                </ul>
                <p v-else class="text-muted">未识别到文章链接，可填写选择器或链接前缀后重试。</p>
              </div>
            </div>
          </template>
          <label v-if="isKeywordKind(form.kind)" class="text-xs font-medium text-ink-2">{{ form.kind === XIAOHEIHE_KEYWORD_MONITOR.kind ? '最低阅读量' : '最低播放量' }} <span class="font-normal text-faint">（可选）</span>
            <input v-model.number="form.min_views" type="number" min="0" max="10000000" step="1" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label v-if="!isHotPosts" class="text-xs font-medium text-ink-2">采集间隔（分钟）
            <input v-model.number="form.interval_minutes" type="number" required :min="customQuota.min_interval_minutes" max="1440" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label v-if="!isHotPosts" class="text-xs font-medium text-ink-2">标题须包含任一词 <span class="font-normal text-faint">（逗号分隔，可选）</span>
            <input v-model="form.includeText" class="input mt-1.5 h-10 w-full font-normal">
          </label>
          <label v-if="!isHotPosts" class="text-xs font-medium text-ink-2">排除词 <span class="font-normal text-faint">（逗号分隔，可选）</span>
            <input v-model="form.excludeText" class="input mt-1.5 h-10 w-full font-normal">
          </label>
        </div>
        <div class="mt-4 flex flex-wrap gap-x-5 gap-y-2">
          <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input v-model="form.is_public" type="checkbox" class="checkbox"> 公开到定制化频道</label>
          <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input v-model="form.push_enabled" type="checkbox" class="checkbox"> 新内容邮件推送</label>
        </div>
        <p class="mt-2 text-xs text-faint">推送需开启邮件通知；每个监控每天最多一封新内容摘要。</p>
        <p v-if="isHotPosts && existingXiaoheihe" class="mt-2 text-sm text-muted">已关注小黑盒热帖。<NuxtLink :to="`/custom/${existingXiaoheihe.id}`" class="link">查看监控</NuxtLink></p>
        <div class="mt-4 flex gap-2">
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
      <p class="text-sm text-muted">还没有定制监控。</p>
      <p class="mt-1 text-xs text-faint">{{ customQuota.admitted ? '点「新建监控」选择一个信源。' : '开通公测名额后即可创建。' }}</p>
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
          <p v-if="row.kind !== XIAOHEIHE_MONITOR.kind" class="mono mt-0.5 truncate text-xs text-faint">{{ row.kind === 'uid' || row.kind === XIAOHEIHE_ACCOUNT_MONITOR.kind ? `UID ${row.target_value}` : row.target_value }}</p>
          <p class="mt-3 flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
            <span><span class="num text-[15px] font-bold text-ink">{{ row.item_count }}</span> 条</span>
            <span v-if="row.kind !== XIAOHEIHE_MONITOR.kind">· 每 {{ row.interval_minutes }} 分钟</span>
            <span>· {{ lastCollected(row) }}</span>
          </p>
          <p v-if="row.enabled && row.last_error" class="mt-1 text-xs text-hot">上次采集失败：{{ row.last_error }}</p>
          <p v-if="row.min_views || row.filters?.include?.length || row.filters?.exclude?.length || row.filters?.selector || row.filters?.link_pattern" class="mt-1 text-xs text-faint">
            <template v-if="row.min_views">{{ row.kind === XIAOHEIHE_KEYWORD_MONITOR.kind ? '阅读' : '播放' }} ≥ {{ row.min_views.toLocaleString('zh-CN') }}　</template>
            <template v-if="row.filters?.selector">区域：{{ row.filters.selector }}　</template>
            <template v-if="row.filters?.link_pattern">链接：{{ row.filters.link_pattern }}　</template>
            <template v-if="row.filters?.include?.length">包含：{{ row.filters.include.join('、') }}　</template>
            <template v-if="row.filters?.exclude?.length">排除：{{ row.filters.exclude.join('、') }}</template>
          </p>
          <div class="min-h-3 flex-1" aria-hidden="true" />
          <div class="flex flex-wrap items-center gap-0.5 border-t border-line-soft pt-2">
            <button v-if="row.kind !== XIAOHEIHE_MONITOR.kind" type="button" :class="action" :disabled="customBusy || row.status !== 'active' || !row.enabled" :title="row.enabled ? '立即刷新采集' : '恢复监控后可手动抓取'" @click="center.triggerCustom(row)"><RefreshCw :size="13" />抓取</button>
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
          <template v-if="row.kind !== XIAOHEIHE_MONITOR.kind">
            <label class="text-xs font-medium text-ink-2">{{ kindFieldLabel(row.kind) }}<input v-model="draft.target_value" required :maxlength="isUrlKind(row.kind) ? 255 : 128" :type="isUrlKind(row.kind) ? 'url' : 'text'" :pattern="row.kind === 'uid' ? '[0-9]+' : undefined" class="input mt-1.5 h-9 w-full font-normal"></label>
            <template v-if="isWebKind(row.kind)">
              <label class="text-xs font-medium text-ink-2">内容选择器（可选）<input v-model="draft.selector" maxlength="200" placeholder="CSS 或 XPath" class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
              <label class="text-xs font-medium text-ink-2">排除选择器（可选）<input v-model="draft.excludeSelector" maxlength="200" placeholder="如 .ads, footer" class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
              <label v-if="row.kind === WEB_LIST_MONITOR.kind" class="text-xs font-medium text-ink-2">链接匹配规则（可选）<input v-model="draft.linkPattern" maxlength="200" placeholder="/blog/ 或 regex:..." class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
              <label v-if="row.kind === WEB_PAGE_MONITOR.kind" class="text-xs font-medium text-ink-2">忽略动态行（逗号分隔，可选）<input v-model="draft.ignoreText" class="input mono mt-1.5 h-9 w-full text-xs font-normal"></label>
            </template>
            <div class="grid grid-cols-2 gap-3">
              <label v-if="isKeywordKind(row.kind)" class="text-xs font-medium text-ink-2">{{ row.kind === XIAOHEIHE_KEYWORD_MONITOR.kind ? '最低阅读量' : '最低播放量' }}<input v-model.number="draft.min_views" type="number" required min="0" max="10000000" class="input mt-1.5 h-9 w-full font-normal"></label>
              <label class="text-xs font-medium text-ink-2">采集间隔（分钟）<input v-model.number="draft.interval_minutes" type="number" required :min="Math.min(row.interval_minutes, customQuota.min_interval_minutes)" max="1440" class="input mt-1.5 h-9 w-full font-normal"></label>
            </div>
            <label class="text-xs font-medium text-ink-2">包含任一词<input v-model="draft.includeText" class="input mt-1.5 h-9 w-full font-normal"></label>
            <label class="text-xs font-medium text-ink-2">排除词<input v-model="draft.excludeText" class="input mt-1.5 h-9 w-full font-normal"></label>
            <p class="text-xs text-faint">修改目标后会保留已采集的内容，之后按新目标采集。</p>
          </template>
          <div class="flex gap-2">
            <button type="submit" class="btn-primary h-9 px-4" :disabled="customBusy">{{ customBusy ? '保存中…' : '保存' }}</button>
            <button type="button" class="h-9 rounded-[10px] px-3 text-sm text-muted hover:bg-chip hover:text-ink" @click="editing = null">取消</button>
          </div>
        </form>
      </li>
    </ul>
  </div>
</template>
