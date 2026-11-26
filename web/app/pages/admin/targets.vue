<script setup lang="ts">
import {
  ChevronDown,
  Download,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-vue-next'
import type { CollectTarget } from '~/utils/admin-types'
import { fmtTime, platformLabels, targetTypeLabels } from '~/utils/admin-types'
import { useAdminCommunities } from '~/utils/admin-communities'
import { apiErrorDetail } from '~/utils/api-errors'
import { colorToHex } from '~/utils/community-palette'
import { TIER_META, tierLabel, webExtractorLabel } from '~/utils/lexicon'
import type { SiteCommunityItem } from '~/composables/useSiteConfig'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '分区与信源 · AniSignal 管理看板' })

interface AdminCommunityRow extends SiteCommunityItem {
  target_count: number
  share_count: number
}
interface DeletedBuiltinItem {
  key: string
  label: string
}
interface PresetItem {
  community: string
  label: string
  domain: string
  count: number
  enabled_count: number
  platforms: string[]
}
interface RunItem {
  id: number
  target_id: number
  started_at: string | null
  status: string
  error: string | null
  items_found: number | null
  items_new: number | null
  items_fetched: number | null
  items_filtered: number | null
}
interface WebPreviewResult {
  mode: string
  url: string
  title: string
  site_name: string | null
  published_at: string | null
  extractor: string
  text_length: number
  text_preview: string
  media_count: number
  detected_feeds: { url: string; title: string; type: string }[]
  spa_warning: boolean
  items: { title: string; url: string; published_at: string | null }[]
}

const { api } = useAdminApi()
const toast = useAdminToast()
const { communities: adminCommunities, labelOf, badgeOf, refreshSiteConfig } = useAdminCommunities()

type PageTab = 'sources' | 'communities' | 'bilibili'
const activeTab = ref<PageTab>('sources')
const runsOpen = ref(false)
const openRowMenuId = ref<number | null>(null)

const targets = ref<CollectTarget[]>([])
const error = ref<string | null>(null)
const busyId = ref<number | null>(null)

/* ---------- B 站登录态（扫码 / 账号密码 / 短信 / 粘贴 Cookie / 退出） ---------- */
const biliSession = ref<{ state: string; message: string; checked_at?: string; source?: string } | null>(null)
const checkingBili = ref(false)
const qrSession = ref<{ qrcode_key: string; qr_image: string; state: string; message: string } | null>(null)
const showCookieForm = ref(false)
const cookieText = ref('')
const cookieSaving = ref(false)
const webLoginMode = ref<'password' | 'sms' | null>(null)
const showMoreBiliLogin = ref(false)

const biliStateClass = computed(() => {
  const state = biliSession.value?.state
  if (state === 'logged_in') return 'text-ok'
  return state === 'missing' || state === 'unconfigured' ? 'text-muted' : 'text-warn'
})
const biliDotClass = computed(() => {
  const state = biliSession.value?.state
  if (state === 'logged_in') return 'bg-ok'
  return !state || state === 'missing' || state === 'unconfigured' ? 'bg-faint' : 'bg-warn'
})

let qrTimer: ReturnType<typeof setInterval> | undefined
function stopQrPoll() {
  if (qrTimer) {
    clearInterval(qrTimer)
    qrTimer = undefined
  }
}

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
    toast.err(exc?.data?.detail ?? exc?.message ?? 'B 站登录状态检测失败')
  } finally {
    checkingBili.value = false
  }
}

async function startQrLogin() {
  stopQrPoll()
  showCookieForm.value = false
  webLoginMode.value = null
  checkingBili.value = true
  try {
    const res = await api<{ qrcode_key: string; qr_image: string }>('/api/admin/bilibili-session/qrcode', { method: 'POST' })
    qrSession.value = { ...res, state: 'waiting', message: '请使用 B 站手机客户端扫码确认' }
    qrTimer = setInterval(async () => {
      if (!qrSession.value) return stopQrPoll()
      try {
        const poll = await api<{ state: string; message: string; session?: any }>(`/api/admin/bilibili-session/qrcode/${qrSession.value.qrcode_key}`)
        if (!qrSession.value) return
        qrSession.value.state = poll.state
        qrSession.value.message = poll.message
        if (poll.state === 'saved') {
          stopQrPoll()
          biliSession.value = poll.session ?? (await api('/api/admin/bilibili-session'))
          qrSession.value = null
          toast.ok('B 站扫码登录成功，凭据已保存')
        } else if (poll.state === 'expired') {
          stopQrPoll()
        }
      } catch (exc: any) {
        stopQrPoll()
        if (qrSession.value) qrSession.value.message = exc?.data?.detail ?? '扫码轮询失败，请重新生成'
      }
    }, 2500)
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '二维码生成失败')
  } finally {
    checkingBili.value = false
  }
}

function closeQrLogin() {
  stopQrPoll()
  qrSession.value = null
}

async function saveManualCookie() {
  if (!cookieText.value.trim() || cookieSaving.value) return
  cookieSaving.value = true
  try {
    biliSession.value = await api('/api/admin/bilibili-session/cookie', {
      method: 'PUT',
      body: { cookie: cookieText.value.trim() },
    })
    cookieText.value = ''
    showCookieForm.value = false
    toast.ok(biliSession.value?.state === 'logged_in' ? 'Cookie 已保存并通过核验' : (biliSession.value?.message ?? '已保存'))
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? 'Cookie 保存失败')
  } finally {
    cookieSaving.value = false
  }
}

async function clearBiliLogin() {
  if (!confirm('确认清除后台保存的 B 站登录凭据？')) return
  checkingBili.value = true
  try {
    biliSession.value = await api('/api/admin/bilibili-session', { method: 'DELETE' })
    toast.ok('已清除 B 站登录凭据')
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? '清除失败')
  } finally {
    checkingBili.value = false
  }
}

/* ---------- 站点分区管理 ---------- */
const communityRows = ref<AdminCommunityRow[]>([])
const deletedBuiltins = ref<DeletedBuiltinItem[]>([])
const communityBusy = ref(false)
const editingCommunityKey = ref<string | null>(null)
const editingCommunity = computed(() => communityRows.value.find(row => row.key === editingCommunityKey.value))
const communityForm = reactive({
  key: '',
  label: '',
  en: '',
  color: 'blue' as SiteCommunityItem['color'],
  domain: 'feed' as SiteCommunityItem['domain'],
  official_bili_mid: '',
  aliases: '',
})

async function loadCommunities() {
  try {
    const res = await api<{ items: AdminCommunityRow[]; deleted_builtins?: DeletedBuiltinItem[] }>('/api/admin/communities')
    communityRows.value = res.items
    deletedBuiltins.value = res.deleted_builtins ?? []
  } catch { /* 静默回退 site-config */ }
}

function resetCommunityForm() {
  editingCommunityKey.value = null
  Object.assign(communityForm, { key: '', label: '', en: '', color: 'blue', domain: 'feed', official_bili_mid: '', aliases: '' })
}

function startEditCommunity(row: AdminCommunityRow) {
  activeTab.value = 'communities'
  editingCommunityKey.value = row.key
  Object.assign(communityForm, {
    key: row.key,
    label: row.label,
    en: row.en,
    color: row.color,
    domain: row.domain || 'game',
    official_bili_mid: row.official_bili_mid || '',
    aliases: (row.aliases || []).join('，'),
  })
}

async function saveCommunity() {
  if (communityBusy.value || !communityForm.label.trim()) return
  const key = communityForm.key.trim().toLowerCase()
  if (!editingCommunityKey.value && !/^[a-z][a-z0-9_-]{0,15}$/.test(key)) {
    toast.err('分区标识需为 1~16 位小写字母、数字、下划线或短横线，且以字母开头')
    return
  }
  communityBusy.value = true
  const payload = {
    label: communityForm.label.trim(),
    en: communityForm.en.trim() || communityForm.label.trim(),
    color: communityForm.color,
    domain: communityForm.domain,
    official_bili_mid: communityForm.official_bili_mid.trim() || null,
    aliases: communityForm.aliases.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    active: editingCommunity.value?.active ?? true,
  }
  try {
    if (editingCommunityKey.value) {
      await api(`/api/admin/communities/${editingCommunityKey.value}`, { method: 'PUT', body: payload })
      toast.ok(`分区「${payload.label}」已更新`)
    } else {
      await api('/api/admin/communities', { method: 'POST', body: { key, ...payload } })
      toast.ok(`分区「${payload.label}」已创建`)
      form.community = key
    }
    resetCommunityForm()
    await Promise.all([loadCommunities(), refreshSiteConfig()])
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '保存分区失败'))
  } finally {
    communityBusy.value = false
  }
}

async function toggleCommunityActive(row: AdminCommunityRow) {
  if (communityBusy.value) return
  if (row.active && communityRows.value.filter(c => c.active).length <= 1) {
    toast.err('全站至少保留一个启用分区')
    return
  }
  communityBusy.value = true
  try {
    await api(`/api/admin/communities/${row.key}`, { method: 'PUT', body: { active: !row.active } })
    toast.ok(row.active ? `已隐藏分区「${row.label}」` : `已启用分区「${row.label}」`)
    await Promise.all([loadCommunities(), refreshSiteConfig()])
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '分区状态更新失败'))
  } finally {
    communityBusy.value = false
  }
}

async function batchCommunities(keys: string[], action: 'enable' | 'disable' | 'delete', purgeTargets = false) {
  if (!keys.length || communityBusy.value) return
  communityBusy.value = true
  try {
    await api('/api/admin/communities/batch', { method: 'POST', body: { keys, action, purge_targets: purgeTargets } })
    const msg = action === 'delete'
      ? `已移除 ${keys.length} 个分区`
      : action === 'enable'
        ? '已启用所选分区'
        : '已隐藏无信源分区'
    toast.ok(msg)
    await Promise.all([loadCommunities(), refreshSiteConfig()])
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '批量调整分区失败'))
  } finally {
    communityBusy.value = false
  }
}

function hideEmptyCommunities() {
  const emptyKeys = communityRows.value.filter(c => c.active && !c.target_count && !c.share_count).map(c => c.key)
  if (!emptyKeys.length) {
    toast.info('当前所有启用分区均已有采集信源')
    return
  }
  if (emptyKeys.length >= communityRows.value.filter(c => c.active).length) emptyKeys.shift()
  if (!emptyKeys.length) return
  void batchCommunities(emptyKeys, 'disable')
}

function deleteEmptyCommunities() {
  const emptyKeys = communityRows.value.filter(c => !c.target_count && !c.share_count).map(c => c.key)
  if (!emptyKeys.length) {
    toast.info('当前没有无信源的空分区')
    return
  }
  const activeCount = communityRows.value.filter(c => c.active).length
  const activeRemoving = communityRows.value.filter(c => c.active && emptyKeys.includes(c.key)).length
  if (activeRemoving >= activeCount) {
    const keepKey = communityRows.value.find(c => c.active)?.key
    const idx = keepKey ? emptyKeys.indexOf(keepKey) : -1
    if (idx >= 0) emptyKeys.splice(idx, 1)
  }
  if (!emptyKeys.length) {
    toast.info('全站至少保留一个启用分区，请先新建分区后再移除最后一个分区')
    return
  }
  if (!confirm(`确认移除 ${emptyKeys.length} 个无信源分区？预设分区移除后仍可随时恢复。`)) return
  void batchCommunities(emptyKeys, 'delete')
}

async function deleteCommunity(row: AdminCommunityRow) {
  if (communityBusy.value) return
  if (row.active && communityRows.value.filter(c => c.active).length <= 1) {
    toast.err('全站至少保留一个启用分区，请先新建或启用其他分区后再删除')
    return
  }
  const kindText = row.builtin ? '预设分区' : '自定义分区'
  const msg = row.target_count > 0
    ? `删除${kindText}「${row.label}」并同时移除其下 ${row.target_count} 个采集信源？历史内容不会删除，删除后前台不再展示。`
    : `确认删除${kindText}「${row.label}」？${row.builtin ? '（预设分区删除后仍可恢复）' : ''}`
  if (!confirm(msg)) return
  communityBusy.value = true
  try {
    await api(`/api/admin/communities/${row.key}`, {
      method: 'DELETE',
      query: { purge_targets: row.target_count > 0 },
    })
    toast.ok(`分区「${row.label}」已删除`)
    if (editingCommunityKey.value === row.key) resetCommunityForm()
    if (communityFilter.value === row.key) communityFilter.value = ''
    await Promise.all([loadCommunities(), refreshSiteConfig(), refresh()])
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '删除分区失败'))
  } finally {
    communityBusy.value = false
  }
}

/* ---------- 预设信源导入 ---------- */
const showPresetPanel = ref(false)
const presets = ref<PresetItem[]>([])
const selectedPresets = ref<string[]>([])
const importingPresets = ref(false)

async function openPresetPanel() {
  showPresetPanel.value = !showPresetPanel.value
  if (showPresetPanel.value && !presets.value.length) {
    try {
      const res = await api<{ items: PresetItem[] }>('/api/admin/collect-targets/presets')
      presets.value = res.items
      selectedPresets.value = res.items.slice(0, 2).map(i => i.community)
    } catch (exc: any) {
      toast.err(exc?.data?.detail ?? '预设目录加载失败')
    }
  }
}

function togglePresetKey(key: string) {
  selectedPresets.value = selectedPresets.value.includes(key)
    ? selectedPresets.value.filter(k => k !== key)
    : [...selectedPresets.value, key]
}

async function importSelectedPresets() {
  if (!selectedPresets.value.length || importingPresets.value) return
  importingPresets.value = true
  try {
    const res = await api<{ added: number; skipped: number; communities: string[] }>('/api/admin/collect-targets/import-presets', {
      method: 'POST',
      body: { communities: selectedPresets.value },
    })
    await api('/api/admin/communities/batch', {
      method: 'POST',
      body: { keys: selectedPresets.value, action: 'enable' },
    })
    toast.ok(`导入完成：新增 ${res.added} 个信源${res.skipped ? `（跳过已存在 ${res.skipped} 个）` : ''}`)
    showPresetPanel.value = false
    await Promise.all([refresh(), loadCommunities(), refreshSiteConfig()])
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '导入预设失败')
  } finally {
    importingPresets.value = false
  }
}

/* ---------- 采集信源抽屉表单与批量操作 ---------- */
const combos: Record<string, string[]> = {
  game_official: ['notices'],
  bilibili: ['keyword_search', 'account_videos', 'account_dynamics'],
  miyoushe: ['keyword_search'],
  xiaoheihe: ['account_dynamics', 'keyword_search', 'feed'],
  kuro_cn: ['notices'],
  kuro_global: ['notices'],
  steam: ['notices'],
  youtube: ['channel_rss'],
  x: ['account_dynamics'],
  rss: ['feed'],
  web_page: ['page_watch', 'list_watch'],
  github: ['releases', 'issues', 'discussions', 'trending'],
  gitcode: ['releases', 'issues'],
}
const collectPlatforms = Object.keys(platformLabels).filter(platform => platform in combos)

const drawerOpen = ref(false)
const drawerAdvancedOpen = ref(false)
const form = reactive({
  platform: 'web_page',
  target_type: 'page_watch',
  target_value: '',
  display_name: '',
  interval_minutes: 60,
  co_terms: '',
  min_views: 0,
  tier: 'core',
  community: 'wuwa',
  is_official: true,
  web_selector: '',
  web_exclude_selector: '',
  web_link_pattern: '',
  web_ignore_patterns: '',
  web_fetch_body: true,
  repo_include_prerelease: true,
  repo_labels: '',
})
const webPreview = ref<WebPreviewResult | null>(null)
const webPreviewing = ref(false)
const webPreviewError = ref<string | null>(null)
const creating = ref(false)
const formError = ref<string | null>(null)
const editingId = ref<number | null>(null)

const communityFilter = ref('')
const filteredTargets = computed(() =>
  communityFilter.value ? targets.value.filter(t => t.community === communityFilter.value) : targets.value,
)

const selectedIds = ref<number[]>([])
watch(communityFilter, () => {
  selectedIds.value = []
  void refreshRuns()
})
const allFilteredSelected = computed(() =>
  filteredTargets.value.length > 0 && filteredTargets.value.every(t => selectedIds.value.includes(t.id)),
)
function toggleSelectAll() {
  if (allFilteredSelected.value) {
    const filteredSet = new Set(filteredTargets.value.map(t => t.id))
    selectedIds.value = selectedIds.value.filter(id => !filteredSet.has(id))
  } else {
    selectedIds.value = [...new Set([...selectedIds.value, ...filteredTargets.value.map(t => t.id)])]
  }
}

const batchBusy = ref(false)
async function runBatchTargets(action: 'enable' | 'disable' | 'delete', opts?: { community?: string }) {
  if (batchBusy.value) return
  if (opts?.community) {
    const label = labelOf(opts.community)
    if (!confirm(`确认移除「${label}」分区下的全部 ${filteredTargets.value.length} 个采集信源？`)) return
  } else {
    if (!selectedIds.value.length) return
    if (action === 'delete' && !confirm(`确认移除选中的 ${selectedIds.value.length} 个采集信源？`)) return
  }
  batchBusy.value = true
  try {
    const body = opts?.community
      ? { community: opts.community, action }
      : { ids: selectedIds.value, action }
    const res = await api<{ affected: number }>('/api/admin/collect-targets/batch', { method: 'POST', body })
    const actionLabel = action === 'delete' ? '移除' : action === 'enable' ? '启用' : '停用'
    toast.ok(`已批量${actionLabel} ${res.affected} 个信源`)
    selectedIds.value = []
    await Promise.all([refresh(), loadCommunities(), refreshRuns()])
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '批量操作失败'))
  } finally {
    batchBusy.value = false
  }
}

const availableTypes = computed(() => combos[form.platform] ?? [])
const isGitPlatform = computed(() => form.platform === 'github' || form.platform === 'gitcode')
const supportsOfficial = computed(() => !['keyword_search', 'topic_search'].includes(form.target_type))
const supportsViews = computed(() => form.community !== 'ai' && (
  (form.platform === 'bilibili' && ['keyword_search', 'account_videos'].includes(form.target_type))
  || (form.platform === 'xiaoheihe' && form.target_type === 'keyword_search')
))
const defaultMinViews = () => (supportsViews.value && form.platform === 'bilibili' && form.target_type === 'keyword_search' ? 10000 : 0)

const targetPlaceholder = computed(() => {
  if (form.platform === 'rss') return 'RSS / Atom 地址，如 https://openai.com/news/rss.xml'
  if (form.platform === 'web_page') {
    return form.target_type === 'list_watch'
      ? '列表页地址，如 https://example.com/news'
      : '目标网页地址，如 https://example.com/changelog'
  }
  if (form.platform === 'github') {
    return form.target_type === 'trending'
      ? 'daily / weekly / monthly（可选语言如 daily:python）'
      : 'owner/repo 或 https://github.com/owner/repo'
  }
  if (form.platform === 'gitcode') return 'owner/repo 或 https://gitcode.com/owner/repo'
  if (form.platform === 'youtube') return 'YouTube 频道链接、@handle 或 UC 开头的频道 ID'
  if (form.platform === 'x') return '@账号 或 https://x.com/账号'
  if (form.platform === 'game_official') return '仅支持 genshin / endfield / nikke'
  if (form.platform === 'kuro_cn' || form.platform === 'kuro_global') return '固定填写 default'
  if (form.platform === 'steam') return 'Steam AppID 或商店链接（如 3513350）'
  if (form.platform === 'bilibili' && form.target_type !== 'keyword_search') return 'B 站 UP 主数字 UID（如 1955897084）'
  if (form.platform === 'xiaoheihe' && form.target_type === 'account_dynamics') return '小黑盒数字 UID 或主页链接（如 79109824）'
  if (form.platform === 'xiaoheihe' && form.target_type === 'feed') return '热帖填 hot，或填小黑盒话题 ID'
  return '搜索关键词（如 鸣潮 周边）'
})

watch(adminCommunities, (list) => {
  if (!editingId.value && list.length && !list.some(c => c.key === form.community)) {
    form.community = (list.find(c => c.active) ?? list[0])!.key
  }
}, { immediate: true })

watch(() => form.platform, () => {
  webPreview.value = null
  webPreviewError.value = null
  if (!editingId.value) {
    form.target_type = availableTypes.value[0] ?? 'keyword_search'
    form.interval_minutes = ['x', 'web_page', 'github', 'gitcode'].includes(form.platform) ? 60 : 30
    form.is_official = (
      form.platform === 'web_page'
      || form.platform === 'game_official'
      || form.platform === 'kuro_cn'
      || form.platform === 'kuro_global'
      || ((form.platform === 'github' || form.platform === 'gitcode') && form.target_type === 'releases')
    )
    form.tier = form.target_type === 'keyword_search' ? 'general' : 'core'
  }
})
watch(() => form.target_type, (next) => {
  webPreview.value = null
  webPreviewError.value = null
  if (next === 'keyword_search') form.is_official = false
  else if (!editingId.value && (form.platform === 'github' || form.platform === 'gitcode')) {
    form.is_official = next === 'releases'
    if (next === 'trending') {
      form.interval_minutes = 360
      if (!form.target_value.trim()) form.target_value = 'daily'
    }
  }
})
watch([() => form.target_type, () => form.community, supportsViews], () => {
  if (!editingId.value) form.min_views = defaultMinViews()
})
watch([() => form.platform, () => form.community], () => {
  if (editingId.value) return
  if (form.platform === 'game_official' && ['genshin', 'endfield', 'nikke'].includes(form.community)) {
    form.target_value = form.community
  } else if (form.platform === 'kuro_cn' || form.platform === 'kuro_global') {
    form.target_value = 'default'
  }
})

function resetFormDefaults() {
  editingId.value = null
  formError.value = null
  webPreview.value = null
  webPreviewError.value = null
  const defaultCommunity = communityFilter.value
    || (adminCommunities.value.find(c => c.active) ?? adminCommunities.value[0])?.key
    || 'wuwa'
  Object.assign(form, {
    platform: 'bilibili',
    target_type: 'keyword_search',
    target_value: '',
    display_name: '',
    interval_minutes: 30,
    co_terms: '',
    min_views: 10000,
    tier: 'general',
    community: defaultCommunity,
    is_official: false,
    web_selector: '',
    web_exclude_selector: '',
    web_link_pattern: '',
    web_ignore_patterns: '',
    web_fetch_body: true,
    repo_include_prerelease: true,
    repo_labels: '',
  })
}

function openCreateDrawer() {
  resetFormDefaults()
  drawerAdvancedOpen.value = false
  drawerOpen.value = true
}

function closeDrawer() {
  drawerOpen.value = false
  editingId.value = null
  formError.value = null
  webPreview.value = null
  webPreviewError.value = null
}

async function edit(target: CollectTarget) {
  openRowMenuId.value = null
  editingId.value = target.id
  formError.value = null
  webPreview.value = null
  webPreviewError.value = null
  const webCfg = target.web_config || {}
  const repoCfg = target.repo_config || {}
  Object.assign(form, {
    platform: target.platform, target_type: target.target_type, target_value: target.target_value,
    display_name: target.display_name || '', interval_minutes: target.interval_minutes,
    co_terms: (target.co_terms || []).join('，'), min_views: target.min_views || 0,
    tier: target.tier, community: target.community, is_official: !!target.is_official,
    web_selector: webCfg.selector || '',
    web_exclude_selector: webCfg.exclude_selector || '',
    web_link_pattern: webCfg.link_pattern || '',
    web_ignore_patterns: (webCfg.ignore_patterns || []).join('\n'),
    web_fetch_body: webCfg.fetch_body !== false,
    repo_include_prerelease: repoCfg.include_prerelease !== false,
    repo_labels: (repoCfg.labels || []).join('，'),
  })
  drawerAdvancedOpen.value = Boolean(
    target.co_terms?.length
    || target.min_views
    || webCfg.selector
    || webCfg.exclude_selector
    || webCfg.link_pattern
    || webCfg.ignore_patterns?.length
    || repoCfg.include_prerelease === false
    || repoCfg.labels?.length,
  )
  drawerOpen.value = true
  await nextTick()
  form.min_views = target.min_views || 0
}

async function refresh() {
  try {
    targets.value = await api<CollectTarget[]>('/api/admin/collect-targets')
    if (targets.value.length === 0) showPresetPanel.value = true
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}

function formatLastError(err: string | null): string {
  if (!err) return '—'
  if (/BiliRiskControlError|风控|-352/.test(err)) {
    return '未登录时动态接口易触发风控，请前往「B 站登录态」页签扫码登录'
  }
  if (/error '(404|5\d\d)\b[^']*' for url '[^']*\/feeds\/videos\.xml/.test(err)) {
    return 'YouTube RSS 间歇性异常，系统将自动重试；也可在 .env 配置 YouTube API Key'
  }
  return err
}

const runs = ref<RunItem[]>([])
const targetName = (targetId: number) =>
  targets.value.find(t => t.id === targetId)?.display_name || `#${targetId}`

async function refreshRuns() {
  try {
    runs.value = await api<RunItem[]>('/api/admin/runs', {
      query: { limit: 8, community: communityFilter.value || undefined },
    })
  } catch { /* 运行记录失败不影响主表 */ }
}

function onDocClick(event: MouseEvent) {
  if (openRowMenuId.value === null) return
  const target = event.target as HTMLElement | null
  if (!target?.closest('[data-target-menu]')) openRowMenuId.value = null
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (openRowMenuId.value !== null) openRowMenuId.value = null
    else if (drawerOpen.value && !creating.value) closeDrawer()
  }
}

onMounted(async () => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
  void loadBiliSession()
  void loadCommunities()
  void refreshRuns()
  await refresh()
  if (showPresetPanel.value && !presets.value.length) {
    try {
      const res = await api<{ items: PresetItem[] }>('/api/admin/collect-targets/presets')
      presets.value = res.items
      selectedPresets.value = res.items.slice(0, 2).map(i => i.community)
    } catch { /* ignore */ }
  }
})
onBeforeUnmount(() => {
  stopQrPoll()
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})

function buildWebConfigPayload() {
  if (form.platform !== 'web_page') return undefined
  const ignores = form.web_ignore_patterns.split(/[,，\n]+/).map(x => x.trim()).filter(Boolean)
  return {
    selector: form.web_selector.trim(),
    exclude_selector: form.web_exclude_selector.trim(),
    link_pattern: form.target_type === 'list_watch' ? form.web_link_pattern.trim() : '',
    ignore_patterns: form.target_type === 'page_watch' ? ignores : [],
    fetch_body: form.target_type === 'list_watch' ? form.web_fetch_body : undefined,
  }
}

function buildRepoConfigPayload() {
  if (!isGitPlatform.value) return undefined
  if (form.target_type === 'releases') {
    return { include_prerelease: form.repo_include_prerelease }
  }
  if (form.target_type === 'issues') {
    return { labels: form.repo_labels.split(/[,，\n]+/).map(x => x.trim()).filter(Boolean) }
  }
  return {}
}

async function previewWebPage() {
  if (form.platform !== 'web_page' || !form.target_value.trim() || webPreviewing.value) return
  webPreviewing.value = true
  webPreviewError.value = null
  try {
    webPreview.value = await api<WebPreviewResult>('/api/admin/web-page/preview', {
      method: 'POST',
      body: {
        url: form.target_value.trim(),
        mode: form.target_type === 'list_watch' ? 'list_watch' : 'page_watch',
        web_config: buildWebConfigPayload(),
      },
    })
    if (!form.display_name.trim() && webPreview.value?.title) {
      form.display_name = webPreview.value.title.slice(0, 64)
    }
  } catch (exc: any) {
    webPreview.value = null
    webPreviewError.value = apiErrorDetail(exc, '网页解析失败，请检查地址后重试')
  } finally {
    webPreviewing.value = false
  }
}

function useDetectedFeed(feedUrl: string, feedTitle: string) {
  if (editingId.value !== null) return
  form.platform = 'rss'
  form.target_type = 'feed'
  form.target_value = feedUrl
  if (!form.display_name.trim() && feedTitle) form.display_name = feedTitle.slice(0, 64)
  webPreview.value = null
  webPreviewError.value = null
  toast.info('已切换为该站点官方 RSS 订阅')
}

async function saveTarget() {
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
        co_terms: form.tier === 'general' ? form.co_terms.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean) : [],
        min_views: supportsViews.value ? Number(form.min_views) || 0 : 0,
        tier: form.tier,
        community: form.community,
        is_official: supportsOfficial.value && form.is_official,
        web_config: buildWebConfigPayload(),
        repo_config: buildRepoConfigPayload(),
      },
    })
    toast.ok(wasEditing ? '采集信源已保存' : '采集信源已创建')
    closeDrawer()
    await Promise.all([refresh(), loadCommunities()])
  } catch (exc: any) {
    formError.value = apiErrorDetail(exc, exc?.statusCode === 409 ? '该分区下已存在相同的采集信源' : '保存失败，请重试')
  } finally {
    creating.value = false
  }
}

async function toggle(target: CollectTarget) {
  openRowMenuId.value = null
  busyId.value = target.id
  try {
    await api(`/api/admin/collect-targets/${target.id}`, {
      method: 'PUT',
      body: { enabled: !target.enabled },
    })
    toast.ok(target.enabled ? '已停用' : '已启用')
    await refresh()
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '操作失败，请重试'))
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
    error.value = `触发失败：${apiErrorDetail(exc, '请求失败')}`
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
    toast.ok(`「${target.display_name || target.target_value}」已切换为${tierLabel(next)}`)
  } catch (exc: any) {
    toast.err(`切换失败：${apiErrorDetail(exc, '请求失败')}`)
  } finally {
    busyId.value = null
  }
}

async function remove(target: CollectTarget) {
  openRowMenuId.value = null
  if (!confirm(`确认删除信源「${target.display_name || target.target_value}」及其运行记录？`)) return
  busyId.value = target.id
  try {
    await api(`/api/admin/collect-targets/${target.id}`, { method: 'DELETE' })
    selectedIds.value = selectedIds.value.filter(id => id !== target.id)
    toast.ok('信源已删除')
    await Promise.all([refresh(), loadCommunities(), refreshRuns()])
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '删除失败，请重试')
  } finally {
    busyId.value = null
  }
}

const rowMenuItem = 'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-ink-2 transition-colors hover:bg-chip hover:text-ink disabled:opacity-40'
</script>

<template>
  <div class="space-y-5">
    <!-- 页头与 3 个工作页签 -->
    <div class="wall-heading flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold text-ink">分区与信源</h1>
        <div class="mt-3 inline-flex items-center gap-1 rounded-xl border border-line bg-card p-1 text-sm" role="tablist" aria-label="分区与信源管理">
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'sources'"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 transition-colors"
            :class="activeTab === 'sources' ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-chip hover:text-ink'"
            @click="activeTab = 'sources'"
          >
            采集信源
            <span class="num text-xs opacity-75">{{ targets.length }}</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'communities'"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 transition-colors"
            :class="activeTab === 'communities' ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-chip hover:text-ink'"
            @click="activeTab = 'communities'"
          >
            站点分区
            <span class="num text-xs opacity-75">{{ communityRows.length || adminCommunities.length }}</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeTab === 'bilibili'"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 transition-colors"
            :class="activeTab === 'bilibili' ? 'bg-ink font-semibold text-card' : 'text-ink-2 hover:bg-chip hover:text-ink'"
            @click="activeTab = 'bilibili'"
          >
            <span class="size-2 rounded-full" :class="biliDotClass" aria-hidden="true" />
            B 站登录态
          </button>
        </div>
      </div>

      <div v-if="activeTab === 'sources'" class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-line bg-card px-3 text-sm text-ink-2 transition-colors hover:bg-chip hover:text-ink"
          @click="openPresetPanel"
        >
          <Download :size="14" /> {{ showPresetPanel ? '收起预设' : '导入预设' }}
        </button>
        <button
          type="button"
          class="btn-primary inline-flex h-9 items-center gap-1.5 px-3.5 text-sm"
          @click="openCreateDrawer"
        >
          <Plus :size="15" /> 新建信源
        </button>
      </div>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-hot">{{ error }}</p>

    <!-- ==================== 页签 1：采集信源 ==================== -->
    <template v-if="activeTab === 'sources'">
      <!-- 预设信源导入（按需展开） -->
      <section v-if="showPresetPanel" class="panel space-y-3 border-brand/30 bg-brand-soft/40 p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 class="text-sm font-semibold text-ink">导入预设信源包</h2>
            <p class="mt-0.5 text-xs text-muted">勾选需要启用的分区即可幂等导入内置官方公告、视频与 RSS 信源，已存在目标自动跳过。</p>
          </div>
          <div class="flex items-center gap-2 text-xs">
            <button type="button" class="text-brand-ink hover:underline" @click="selectedPresets = presets.map(p => p.community)">全选</button>
            <span class="text-faint">·</span>
            <button type="button" class="text-muted hover:underline" @click="selectedPresets = []">清空</button>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="pack in presets"
            :key="pack.community"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs transition-colors"
            :class="selectedPresets.includes(pack.community) ? 'border-brand bg-brand font-semibold text-white' : 'border-line bg-card text-ink-2 hover:text-ink'"
            @click="togglePresetKey(pack.community)"
          >
            <span>{{ pack.label }}</span>
            <span class="mono opacity-80">{{ pack.count }} 源</span>
          </button>
        </div>
        <div class="flex flex-wrap items-center gap-3 pt-1">
          <button
            type="button"
            class="btn-primary h-9 px-4 text-sm"
            :disabled="!selectedPresets.length || importingPresets"
            @click="importSelectedPresets"
          >
            {{ importingPresets ? '正在导入…' : `导入所选 ${selectedPresets.length} 个分区` }}
          </button>
        </div>
      </section>

      <!-- 分区筛选与批量操作栏 -->
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-1.5">
          <button
            v-for="option in [['', '全部分区'], ...adminCommunities.map(c => [c.key, c.label] as const)]"
            :key="option[0]"
            type="button"
            class="h-7 rounded-full px-3 text-xs transition-colors"
            :class="communityFilter === option[0] ? 'bg-brand font-semibold text-white' : 'bg-chip text-muted hover:text-ink'"
            @click="communityFilter = option[0]"
          >
            {{ option[1] }}
          </button>
        </div>

        <div class="flex flex-wrap items-center gap-2 text-xs">
          <template v-if="selectedIds.length">
            <span class="text-muted">已选 {{ selectedIds.length }} 项</span>
            <button type="button" class="rounded-md bg-emerald-500/10 px-2.5 py-1 font-medium text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-300" :disabled="batchBusy" @click="runBatchTargets('enable')">批量启用</button>
            <button type="button" class="rounded-md bg-chip px-2.5 py-1 text-ink-2 hover:bg-line" :disabled="batchBusy" @click="runBatchTargets('disable')">批量停用</button>
            <button type="button" class="rounded-md bg-red-500/10 px-2.5 py-1 font-medium text-red-600 hover:bg-red-500/20 dark:text-red-300" :disabled="batchBusy" @click="runBatchTargets('delete')">移除所选</button>
          </template>
          <button
            v-else-if="communityFilter && filteredTargets.length"
            type="button"
            class="rounded-md border border-red-500/30 px-2.5 py-1 text-red-600 hover:bg-red-500/10 dark:text-red-300"
            :disabled="batchBusy"
            @click="runBatchTargets('delete', { community: communityFilter })"
          >
            清空「{{ labelOf(communityFilter) }}」全部信源
          </button>
        </div>
      </div>

      <!-- 精简 6 列信源表 -->
      <div class="panel overflow-x-auto lg:overflow-visible">
        <table class="w-full min-w-[660px] text-left text-[13px]">
          <thead>
            <tr class="border-b border-line text-xs text-muted">
              <th class="w-9 px-2.5 py-2.5">
                <input type="checkbox" class="checkbox" :checked="allFilteredSelected" :disabled="!filteredTargets.length" aria-label="全选当前列表" @change="toggleSelectAll">
              </th>
              <th class="whitespace-nowrap px-2.5 py-2.5 font-medium">信源与分区</th>
              <th class="whitespace-nowrap px-2.5 py-2.5 font-medium">平台与类型</th>
              <th class="whitespace-nowrap px-2.5 py-2.5 font-medium">收录策略</th>
              <th class="whitespace-nowrap px-2.5 py-2.5 font-medium">频率与最近成功</th>
              <th class="whitespace-nowrap px-2.5 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line-soft">
            <tr v-for="(target, rowIdx) in filteredTargets" :key="target.id" :class="target.enabled ? '' : 'opacity-55'">
              <td class="px-2.5 py-3 align-top">
                <input v-model="selectedIds" type="checkbox" :value="target.id" class="checkbox mt-0.5" :aria-label="`选择 ${target.display_name || target.target_value}`">
              </td>
              <td class="w-full max-w-0 px-2.5 py-3 align-top">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="truncate font-semibold text-ink">{{ target.display_name && target.display_name !== target.target_value ? target.display_name : target.target_value }}</span>
                  <span class="chip whitespace-nowrap" :class="badgeOf(target.community)">{{ labelOf(target.community) }}</span>
                  <span v-if="target.is_official" class="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">官方</span>
                  <span v-if="!target.enabled" class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted">已停用</span>
                </div>
                <div v-if="(target.display_name && target.display_name !== target.target_value) || target.web_config?.selector || target.repo_config?.include_prerelease === false || target.repo_config?.labels?.length" class="mono mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-faint">
                  <span v-if="target.display_name && target.display_name !== target.target_value" class="min-w-0 truncate" :title="target.target_value">{{ target.target_value }}</span>
                  <span v-if="target.web_config?.selector" class="shrink-0 rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted" :title="`内容区域：${target.web_config.selector}`">{{ target.web_config.selector }}</span>
                  <span v-if="target.repo_config?.include_prerelease === false" class="shrink-0 rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted">仅正式版</span>
                  <span v-if="target.repo_config?.labels?.length" class="shrink-0 rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted" :title="`限定标签：${target.repo_config.labels.join('、')}`">标签 {{ target.repo_config.labels.join(' / ') }}</span>
                </div>
                <p v-if="target.last_error" class="mt-1 truncate text-xs text-warn" :title="target.last_error">
                  {{ formatLastError(target.last_error) }}
                </p>
              </td>
              <td class="whitespace-nowrap px-2.5 py-3 align-top text-ink-2">
                <div>{{ platformLabels[target.platform] ?? target.platform }}</div>
                <div
                  v-if="(targetTypeLabels[target.target_type] ?? target.target_type) !== (platformLabels[target.platform] ?? target.platform)"
                  class="mt-0.5 text-xs text-faint"
                >
                  {{ targetTypeLabels[target.target_type] ?? target.target_type }}
                </div>
              </td>
              <td class="whitespace-nowrap px-2.5 py-3 align-top">
                <button
                  type="button"
                  class="chip cursor-pointer whitespace-nowrap transition-colors"
                  :class="target.tier === 'core' ? 'bg-brand-soft text-brand-ink' : 'bg-chip text-muted'"
                  :title="`${TIER_META[target.tier]?.desc ?? ''}，点击切换`"
                  :disabled="busyId === target.id"
                  @click="toggleTier(target)"
                >
                  {{ tierLabel(target.tier) }}
                </button>
                <div v-if="target.min_views || target.co_terms?.length" class="mt-1 text-[11px] text-faint">
                  <span v-if="target.min_views" class="mono">≥ {{ target.min_views.toLocaleString('zh-CN') }}</span>
                  <span v-if="target.min_views && target.co_terms?.length"> · </span>
                  <span v-if="target.co_terms?.length" :title="target.co_terms.join('、')">必含 {{ target.co_terms.length }} 词</span>
                </div>
              </td>
              <td class="whitespace-nowrap px-2.5 py-3 align-top text-xs">
                <div class="mono tabular-nums text-ink-2">
                  {{ target.platform === 'x' ? '约 ' : '' }}{{ target.interval_minutes }} 分钟
                  <span v-if="target.total_items_new != null" class="text-faint">· 累计 {{ target.total_items_new }}</span>
                </div>
                <div class="mono mt-0.5 tabular-nums text-faint">{{ fmtTime(target.last_success_at) }}</div>
              </td>
              <td class="whitespace-nowrap px-2.5 py-3 text-right align-top">
                <div class="inline-flex items-center gap-0.5">
                  <button
                    type="button"
                    class="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium text-brand-ink transition-colors hover:bg-brand-soft disabled:opacity-40"
                    :disabled="busyId === target.id"
                    @click="runNow(target)"
                  >
                    <RefreshCw :size="12" /> 采集
                  </button>
                  <div class="relative" data-target-menu>
                    <button
                      type="button"
                      class="grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-chip hover:text-ink"
                      :aria-expanded="openRowMenuId === target.id"
                      aria-haspopup="menu"
                      :aria-label="`更多操作：${target.display_name || target.target_value}`"
                      @click="openRowMenuId = openRowMenuId === target.id ? null : target.id"
                    >
                      <MoreHorizontal :size="15" />
                    </button>
                    <div
                      v-if="openRowMenuId === target.id"
                      role="menu"
                      class="popover absolute right-0 z-30 w-32 p-1"
                      :class="filteredTargets.length > 2 && rowIdx >= filteredTargets.length - 2 ? 'bottom-full mb-1' : 'top-full mt-1'"
                    >
                      <button type="button" role="menuitem" :class="rowMenuItem" :disabled="busyId === target.id" @click="edit(target)">
                        <Pencil :size="12" /> 编辑配置
                      </button>
                      <button type="button" role="menuitem" :class="rowMenuItem" :disabled="busyId === target.id" @click="toggle(target)">
                        <Pause v-if="target.enabled" :size="12" /><Play v-else :size="12" />{{ target.enabled ? '停用信源' : '启用信源' }}
                      </button>
                      <div class="my-1 border-t border-line-soft" />
                      <button
                        type="button"
                        role="menuitem"
                        class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-hot transition-colors hover:bg-hot/10 disabled:opacity-40"
                        :disabled="busyId === target.id"
                        @click="remove(target)"
                      >
                        <Trash2 :size="12" /> 删除信源
                      </button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
            <tr v-if="filteredTargets.length === 0">
              <td colspan="6" class="px-4 py-10 text-center text-sm text-faint">
                {{ targets.length === 0 ? '暂无采集信源，可点击右上角「导入预设」或「新建信源」' : '当前筛选分区下暂无信源' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 最近采集运行（默认折叠，按需展开） -->
      <section class="panel overflow-hidden">
        <button
          type="button"
          class="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-chip/40"
          :aria-expanded="runsOpen"
          @click="runsOpen = !runsOpen"
        >
          <span class="flex items-center gap-2 text-sm font-semibold text-ink">
            最近采集运行
            <span class="num rounded bg-chip px-1.5 py-0.5 text-xs font-normal text-muted">{{ runs.length }}</span>
          </span>
          <span class="inline-flex items-center gap-1 text-xs text-muted">
            {{ runsOpen ? '收起' : '展开记录' }}
            <ChevronDown :size="14" class="transition-transform" :class="{ 'rotate-180': runsOpen }" />
          </span>
        </button>
        <div v-if="runsOpen" class="overflow-x-auto border-t border-line-soft">
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="run in runs" :key="run.id" class="border-t border-line-soft first:border-0 text-xs">
                <td class="whitespace-nowrap px-4 py-2 text-ink-2">{{ targetName(run.target_id) }}</td>
                <td class="mono whitespace-nowrap tabular-nums px-4 py-2 text-faint">{{ fmtTime(run.started_at) }}</td>
                <td class="whitespace-nowrap px-4 py-2">
                  <span class="chip" :class="run.status === 'success' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' : run.status === 'failed' ? 'bg-red-500/10 text-red-600 dark:text-red-400' : 'bg-chip text-muted'">{{ run.status === 'skipped' ? '冷却跳过' : run.status }}</span>
                </td>
                <td class="px-4 py-2 text-muted" :title="run.error ?? ''">
                  <span class="mono">抓取 {{ run.items_fetched ?? run.items_found ?? '—' }} · 过滤 {{ run.items_filtered ?? 0 }} · 入库 {{ run.items_new ?? 0 }}</span>
                  <span v-if="run.error" class="ml-2 text-hot">{{ formatLastError(run.error) }}</span>
                </td>
              </tr>
              <tr v-if="!runs.length">
                <td colspan="4" class="px-4 py-6 text-center text-sm text-faint">暂无运行记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <!-- ==================== 页签 2：站点分区 ==================== -->
    <section v-else-if="activeTab === 'communities'" class="panel space-y-4 p-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-ink">站点分区配置</h2>
          <p class="mt-0.5 text-xs text-muted">编辑分区名称、主题色或启停状态；隐藏或删除后将同步暂停对应采集调度。</p>
        </div>
        <div class="flex flex-wrap gap-2 text-xs">
          <button type="button" class="rounded-md border border-line px-2.5 py-1.5 hover:bg-chip" :disabled="communityBusy" @click="hideEmptyCommunities">
            隐藏无信源分区
          </button>
          <button type="button" class="rounded-md border border-red-500/30 px-2.5 py-1.5 text-red-600 hover:bg-red-500/10 dark:text-red-300" :disabled="communityBusy" @click="deleteEmptyCommunities">
            移除无信源分区
          </button>
          <button type="button" class="rounded-md border border-line px-2.5 py-1.5 hover:bg-chip" :disabled="communityBusy" @click="batchCommunities(communityRows.map(c => c.key), 'enable')">
            全部启用
          </button>
        </div>
      </div>

      <div class="flex flex-wrap gap-2">
        <div
          v-for="row in communityRows"
          :key="row.key"
          class="inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs transition-colors"
          :class="row.active ? 'border-line bg-card text-ink' : 'border-line-soft bg-chip/50 text-faint opacity-75'"
        >
          <span class="size-2.5 shrink-0 rounded-full" :style="{ backgroundColor: colorToHex(row.color) }" aria-hidden="true" />
          <span class="font-medium">{{ row.label }}</span>
          <span class="mono text-[11px] text-faint">({{ row.target_count }})</span>
          <span v-if="row.domain === 'feed'" class="text-faint">主题流<span v-if="row.share_count"> · {{ row.share_count }} 篇</span></span>
          <span v-else-if="!row.builtin" class="rounded bg-brand-soft px-1 py-0.5 text-[10px] text-brand-ink">自建</span>
          <button type="button" class="ml-1 text-brand-ink hover:underline" :disabled="communityBusy" @click="toggleCommunityActive(row)">
            {{ row.active ? '隐藏' : '启用' }}
          </button>
          <button type="button" class="text-muted hover:text-ink hover:underline" :disabled="communityBusy" @click="startEditCommunity(row)">编辑</button>
          <button type="button" class="text-hot hover:underline" :disabled="communityBusy" @click="deleteCommunity(row)">删除</button>
        </div>
      </div>

      <div v-if="deletedBuiltins.length" class="flex flex-wrap items-center gap-1.5 rounded-xl bg-chip/50 px-3 py-2 text-xs text-muted">
        <span>已移除预设分区：</span>
        <button
          v-for="item in deletedBuiltins"
          :key="item.key"
          type="button"
          class="rounded-md border border-line bg-card px-2 py-0.5 text-ink-2 transition-colors hover:border-brand hover:text-brand-ink"
          :disabled="communityBusy"
          @click="batchCommunities([item.key], 'enable')"
        >
          + 恢复 {{ item.label }}
        </button>
        <button
          v-if="deletedBuiltins.length > 1"
          type="button"
          class="ml-auto text-brand-ink hover:underline"
          :disabled="communityBusy"
          @click="batchCommunities(deletedBuiltins.map(d => d.key), 'enable')"
        >
          恢复全部预设
        </button>
      </div>

      <form class="grid gap-2.5 border-t border-line pt-4 sm:grid-cols-4" @submit.prevent="saveCommunity">
        <input
          v-model="communityForm.key"
          :disabled="!!editingCommunityKey"
          maxlength="16"
          placeholder="分区标识（1~16 位，如 steam / tech）"
          aria-label="分区英文标识"
          class="input mono px-3 py-2 text-sm disabled:opacity-50"
        >
        <input
          v-model="communityForm.label"
          placeholder="分区名称（如 Steam 独游 / 数码硬件）"
          aria-label="分区中文名称"
          required
          class="input px-3 py-2 text-sm"
        >
        <input
          v-model="communityForm.en"
          placeholder="英文副标题（可选）"
          aria-label="英文副标题"
          class="input px-3 py-2 text-sm"
        >
        <select v-model="communityForm.domain" :disabled="editingCommunity?.builtin" aria-label="分区模式" class="select px-3 py-2 text-sm">
          <option value="feed">主题流</option>
          <option value="game">游戏雷达</option>
          <option v-if="editingCommunity?.domain === 'news'" value="news">AI 快讯</option>
        </select>
        <input
          v-if="communityForm.domain !== 'feed'"
          v-model="communityForm.official_bili_mid"
          placeholder="官方 B 站 UID（可选）"
          aria-label="官方 B 站 UID"
          class="input mono px-3 py-2 text-sm"
        >
        <AdminCommunityColorPicker v-model="communityForm.color" class="sm:col-span-4" />
        <input
          v-model="communityForm.aliases"
          placeholder="关键词别名，逗号分隔（如 BA，Blue Archive；用于跨源搜索聚合）"
          aria-label="关键词别名"
          class="input px-3 py-2 text-sm sm:col-span-3"
        >
        <div class="flex items-center gap-2">
          <button type="submit" class="btn-primary flex-1 px-3 py-2 text-sm" :disabled="communityBusy || !communityForm.label.trim()">
            {{ editingCommunityKey ? '保存分区' : '新建分区' }}
          </button>
          <button v-if="editingCommunityKey" type="button" class="text-xs text-muted hover:underline" @click="resetCommunityForm">取消</button>
        </div>
      </form>
    </section>

    <!-- ==================== 页签 3：B 站登录态 ==================== -->
    <section v-else class="panel space-y-4 p-5" aria-live="polite">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold text-ink">B 站采集登录态</h2>
          <p class="mt-1 text-sm font-medium" :class="biliStateClass">
            {{ biliSession?.message ?? '正在检测当前登录状态…' }}
          </p>
          <p v-if="biliSession?.checked_at" class="mono mt-1 text-xs text-muted">最近检测：{{ fmtTime(biliSession.checked_at) }}</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="btn-primary h-9 px-3.5 text-sm" :disabled="checkingBili" @click="startQrLogin">
            B 站扫码登录
          </button>
          <template v-if="showMoreBiliLogin">
            <button type="button" class="h-9 rounded-md border border-line px-3 text-sm hover:bg-chip" :disabled="checkingBili" @click="webLoginMode = 'password'; showCookieForm = false; closeQrLogin()">
              账号密码登录
            </button>
            <button type="button" class="h-9 rounded-md border border-line px-3 text-sm hover:bg-chip" :disabled="checkingBili" @click="webLoginMode = 'sms'; showCookieForm = false; closeQrLogin()">
              短信验证码登录
            </button>
            <button type="button" class="h-9 rounded-md border border-line px-3 text-sm hover:bg-chip" @click="showCookieForm = !showCookieForm; webLoginMode = null; closeQrLogin()">
              粘贴 Cookie
            </button>
          </template>
          <button v-else type="button" class="h-9 rounded-md border border-line px-3 text-sm text-muted hover:bg-chip" @click="showMoreBiliLogin = true">
            更多登录方式
          </button>
          <button type="button" class="h-9 rounded-md border border-line px-3 text-sm hover:bg-chip" :disabled="checkingBili" @click="loadBiliSession(true)">
            {{ checkingBili ? '检测中…' : '检测登录态' }}
          </button>
          <button v-if="biliSession?.state === 'logged_in'" type="button" class="h-9 rounded-md px-2.5 text-xs text-hot hover:bg-hot/10" :disabled="checkingBili" @click="clearBiliLogin">
            清除凭据
          </button>
        </div>
      </div>

      <div v-if="qrSession" class="flex flex-wrap items-center gap-4 rounded-xl border border-line bg-chip/40 p-4">
        <img :src="qrSession.qr_image" alt="B 站登录二维码" class="size-36 rounded-lg bg-white p-1.5">
        <div class="space-y-1.5 text-sm">
          <p class="font-medium text-ink">{{ qrSession.message }}</p>
          <p class="text-xs text-muted">使用哔哩哔哩手机客户端扫码确认，凭据持久化保存，重启容器不丢失。</p>
          <div class="flex gap-3 pt-1 text-xs">
            <button v-if="qrSession.state === 'expired'" type="button" class="text-brand-ink hover:underline" @click="startQrLogin">重新生成二维码</button>
            <button type="button" class="text-muted hover:underline" @click="closeQrLogin">关闭</button>
          </div>
        </div>
      </div>

      <AdminBilibiliWebLogin v-if="webLoginMode" :key="webLoginMode" :mode="webLoginMode" @close="webLoginMode = null" @login="biliSession = $event; webLoginMode = null" />

      <form v-if="showCookieForm" class="space-y-2 rounded-xl border border-line bg-chip/40 p-4" @submit.prevent="saveManualCookie">
        <p class="text-xs text-muted">支持粘贴浏览器请求头中的 Cookie 字符串或 JSON（需含 <code class="mono">SESSDATA</code>）：</p>
        <textarea
          v-model="cookieText"
          rows="2"
          placeholder="SESSDATA=...; bili_jct=...; DedeUserID=..."
          class="textarea mono w-full px-3 py-2 text-xs"
        />
        <div class="flex items-center gap-2">
          <button type="submit" class="btn-primary px-3.5 py-1.5 text-xs" :disabled="cookieSaving || !cookieText.trim()">
            {{ cookieSaving ? '核验中…' : '保存并核验 Cookie' }}
          </button>
          <button type="button" class="text-xs text-muted hover:underline" @click="showCookieForm = false">取消</button>
        </div>
      </form>
    </section>

    <!-- ==================== 右侧抽屉：新建 / 编辑采集信源 ==================== -->
    <Teleport to="body">
      <div v-if="drawerOpen" class="fixed inset-0 z-50 flex justify-end">
        <div class="fixed inset-0 bg-black/40 backdrop-blur-[1px]" aria-hidden="true" @click="!creating && closeDrawer()" />
        <aside
          role="dialog"
          aria-modal="true"
          :aria-label="editingId ? `编辑采集信源 #${editingId}` : '新建采集信源'"
          class="relative z-10 flex h-full w-full max-w-lg flex-col border-l border-line bg-card text-ink shadow-2xl"
        >
          <header class="flex items-center justify-between border-b border-line px-5 py-4">
            <h2 class="text-base font-bold text-ink">{{ editingId ? `编辑采集信源 #${editingId}` : '新建采集信源' }}</h2>
            <button type="button" class="grid size-8 place-items-center rounded-lg text-muted hover:bg-chip hover:text-ink" aria-label="关闭抽屉" @click="!creating && closeDrawer()">
              <X :size="17" />
            </button>
          </header>

          <form id="target-drawer-form" class="flex-1 space-y-4 overflow-y-auto px-5 py-4" @submit.prevent="saveTarget">
            <!-- 核心字段 -->
            <div class="grid gap-3 sm:grid-cols-2">
              <label class="text-xs font-medium text-ink-2">所属分区
                <select v-model="form.community" class="select mt-1.5 h-10 w-full px-3 text-sm font-normal">
                  <option v-for="c in adminCommunities" :key="c.key" :value="c.key">{{ c.label }}{{ c.active ? '' : '（已隐藏）' }}</option>
                </select>
              </label>
              <label class="text-xs font-medium text-ink-2">平台
                <select v-model="form.platform" :disabled="editingId !== null" class="select mt-1.5 h-10 w-full px-3 text-sm font-normal disabled:opacity-55">
                  <option v-for="value in collectPlatforms" :key="value" :value="value">{{ platformLabels[value] }}</option>
                </select>
              </label>
              <label v-if="availableTypes.length > 1" class="text-xs font-medium text-ink-2 sm:col-span-2">采集类型
                <select v-model="form.target_type" :disabled="editingId !== null" class="select mt-1.5 h-10 w-full px-3 text-sm font-normal disabled:opacity-55">
                  <option v-for="type in availableTypes" :key="type" :value="type">{{ targetTypeLabels[type] ?? type }}</option>
                </select>
              </label>
            </div>

            <div>
              <label class="block text-xs font-medium text-ink-2">{{ form.platform === 'x' ? 'X 账号' : form.target_type === 'trending' ? '榜单周期' : isGitPlatform ? '开源仓库' : '采集目标' }}</label>
              <div class="mt-1.5 flex gap-2">
                <input
                  v-model="form.target_value"
                  required
                  :placeholder="targetPlaceholder"
                  class="input h-10 min-w-0 flex-1 px-3 text-sm"
                >
                <button
                  v-if="form.platform === 'web_page'"
                  type="button"
                  class="h-10 shrink-0 rounded-[10px] border border-line bg-card px-3.5 text-xs font-medium text-brand-ink transition-colors hover:bg-chip disabled:opacity-50"
                  :disabled="webPreviewing || !form.target_value.trim()"
                  @click="previewWebPage"
                >
                  {{ webPreviewing ? '解析中…' : '测试解析' }}
                </button>
              </div>
            </div>

            <!-- 网页解析测试预览 -->
            <template v-if="form.platform === 'web_page'">
              <p v-if="webPreviewError" class="text-xs text-hot">{{ webPreviewError }}</p>
              <div v-if="webPreview" class="space-y-2 rounded-xl border border-line bg-canvas/60 p-3 text-xs">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-semibold text-ink">{{ webPreview.title }}</span>
                  <span class="rounded bg-chip px-1.5 py-0.5 text-[11px] text-muted" :title="`底层引擎：${webPreview.extractor}`">{{ webExtractorLabel(webPreview.extractor) }}</span>
                  <span v-if="webPreview.mode === 'page_watch'" class="text-muted">正文 {{ webPreview.text_length }} 字 · {{ webPreview.media_count }} 图</span>
                  <span v-else class="text-muted">识别 {{ webPreview.items.length }} 条链接</span>
                </div>
                <p v-if="webPreview.spa_warning" class="rounded bg-amber-500/10 px-2.5 py-1.5 text-amber-700 dark:text-amber-300">
                  页面静态 HTML 内容极少，可能为前端脚本渲染，建议优先改用 RSS 订阅
                </p>
                <div v-if="webPreview.detected_feeds.length" class="flex flex-wrap items-center gap-1.5 rounded bg-emerald-500/10 px-2.5 py-1.5 text-emerald-700 dark:text-emerald-300">
                  <span>检测到官方 RSS：</span>
                  <button
                    v-for="f in webPreview.detected_feeds"
                    :key="f.url"
                    type="button"
                    class="rounded border border-emerald-500/40 bg-card px-2 py-0.5 font-medium hover:bg-emerald-500/10 disabled:opacity-50"
                    :disabled="editingId !== null"
                    @click="useDetectedFeed(f.url, f.title)"
                  >
                    改用 {{ f.title || 'RSS 订阅' }}
                  </button>
                </div>
                <pre v-if="webPreview.mode === 'page_watch'" class="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-card p-2 font-sans leading-relaxed text-ink-2">{{ webPreview.text_preview || '（未提取到正文）' }}</pre>
                <ul v-else-if="webPreview.items.length" class="max-h-40 space-y-1 overflow-auto">
                  <li v-for="(item, idx) in webPreview.items" :key="idx" class="flex items-baseline justify-between gap-2 border-t border-line-soft pt-1 first:border-0 first:pt-0">
                    <a :href="item.url" target="_blank" rel="noopener" class="truncate text-ink-2 hover:text-brand-ink">{{ item.title }}</a>
                    <span v-if="item.published_at" class="mono shrink-0 text-[11px] text-faint">{{ item.published_at.slice(0, 10) }}</span>
                  </li>
                </ul>
                <p v-else class="text-muted">未识别到文章链接，可在下方高级选项中调整内容区域或链接规则</p>
              </div>
            </template>

            <div class="grid gap-3 sm:grid-cols-2">
              <label class="text-xs font-medium text-ink-2 sm:col-span-2">显示名称
                <input v-model="form.display_name" placeholder="可选，如 OpenAI 官方博客" class="input mt-1.5 h-10 w-full px-3 text-sm font-normal">
              </label>
              <label class="text-xs font-medium text-ink-2 sm:col-span-2">收录策略
                <select v-model="form.tier" class="select mt-1.5 h-10 w-full px-3 text-sm font-normal">
                  <option value="core">全部收录（全量入库，不经词表与热度过滤）</option>
                  <option value="general">条件筛选（按标题必含词与热度门槛筛选）</option>
                </select>
              </label>
            </div>

            <label v-if="supportsOfficial" class="flex cursor-pointer items-center gap-2 text-sm text-ink-2">
              <input v-model="form.is_official" type="checkbox" class="checkbox">
              <span>标记为官方来源</span>
            </label>

            <!-- 折叠高级选项（仅渲染当前平台与策略适用的字段） -->
            <div class="border-t border-line-soft pt-3">
              <button
                type="button"
                class="inline-flex items-center gap-1.5 text-xs font-medium text-muted transition-colors hover:text-ink"
                :aria-expanded="drawerAdvancedOpen"
                @click="drawerAdvancedOpen = !drawerAdvancedOpen"
              >
                <ChevronDown :size="14" class="transition-transform" :class="{ 'rotate-180': drawerAdvancedOpen }" />
                高级选项
              </button>

              <div v-if="drawerAdvancedOpen" class="mt-3 space-y-3">
                <div class="grid gap-3 sm:grid-cols-2">
                  <label class="text-xs font-medium text-ink-2">采集间隔（分钟）
                    <input v-model.number="form.interval_minutes" type="number" :min="form.platform === 'x' ? 30 : 5" max="1440" class="input mono mt-1.5 h-9 w-full px-3 text-sm font-normal">
                  </label>
                  <label v-if="supportsViews" class="text-xs font-medium text-ink-2">最低播放量
                    <input v-model.number="form.min_views" type="number" min="0" class="input mono mt-1.5 h-9 w-full px-3 text-sm font-normal">
                  </label>
                </div>

                <label v-if="form.tier === 'general'" class="block text-xs font-medium text-ink-2">标题必含词
                  <input v-model="form.co_terms" placeholder="逗号分隔，如 键盘, 开箱, 发货（留空不限制）" class="input mt-1.5 h-9 w-full px-3 text-sm font-normal">
                </label>

                <template v-if="form.platform === 'web_page'">
                  <label class="block text-xs font-medium text-ink-2">内容区域
                    <input v-model="form.web_selector" maxlength="200" placeholder="留空自动提取正文，支持 CSS / XPath（如 main、#pricing）" class="input mono mt-1.5 h-9 w-full px-3 text-xs font-normal">
                  </label>
                  <label class="block text-xs font-medium text-ink-2">排除区域
                    <input v-model="form.web_exclude_selector" maxlength="200" placeholder="如 .ads, footer, nav" class="input mono mt-1.5 h-9 w-full px-3 text-xs font-normal">
                  </label>
                  <template v-if="form.target_type === 'list_watch'">
                    <label class="block text-xs font-medium text-ink-2">链接规则
                      <input v-model="form.web_link_pattern" maxlength="200" placeholder="仅收录匹配路径，如 /blog/ 或 regex:/news/2026" class="input mono mt-1.5 h-9 w-full px-3 text-xs font-normal">
                    </label>
                    <label class="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
                      <input v-model="form.web_fetch_body" type="checkbox" class="checkbox">
                      <span>新发现文章自动补读正文与发布时间</span>
                    </label>
                  </template>
                  <label v-if="form.target_type === 'page_watch'" class="block text-xs font-medium text-ink-2">忽略内容
                    <input v-model="form.web_ignore_patterns" placeholder="逗号分隔，如 在线人数, 浏览量" class="input mono mt-1.5 h-9 w-full px-3 text-xs font-normal">
                  </label>
                </template>

                <template v-if="isGitPlatform">
                  <label v-if="form.target_type === 'releases'" class="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
                    <input v-model="form.repo_include_prerelease" type="checkbox" class="checkbox">
                    <span>包含预发布版本（alpha / beta / rc）</span>
                  </label>
                  <label v-if="form.target_type === 'issues'" class="block text-xs font-medium text-ink-2">限定标签
                    <input v-model="form.repo_labels" placeholder="逗号分隔，如 announcement, roadmap, release（留空不限）" class="input mono mt-1.5 h-9 w-full px-3 text-xs font-normal">
                  </label>
                </template>
              </div>
            </div>

            <p v-if="formError" class="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-hot">{{ formError }}</p>
          </form>

          <footer class="flex items-center justify-end gap-2 border-t border-line px-5 py-3.5">
            <button type="button" class="h-9 rounded-[10px] px-3.5 text-sm text-muted hover:bg-chip hover:text-ink" :disabled="creating" @click="closeDrawer">
              取消
            </button>
            <button type="submit" form="target-drawer-form" class="btn-primary h-9 px-4 text-sm" :disabled="creating || !form.target_value.trim()">
              {{ creating ? '保存中…' : editingId ? '保存更改' : '创建信源' }}
            </button>
          </footer>
        </aside>
      </div>
    </Teleport>
  </div>
</template>
