<script setup lang="ts">
import type { CollectTarget } from '~/utils/admin-types'
import { fmtTime, platformLabels, targetTypeLabels } from '~/utils/admin-types'
import { useAdminCommunities } from '~/utils/admin-communities'
import { apiErrorDetail } from '~/utils/api-errors'
import type { SiteCommunityItem } from '~/composables/useSiteConfig'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '采集目标与分区 · AniSignal 管理看板' })

interface AdminCommunityRow extends SiteCommunityItem {
  target_count: number
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
  items_found: number | null
  items_new: number | null
  items_fetched: number | null
  items_filtered: number | null
}

const COLOR_OPTIONS = [
  { value: 'yellow', label: '琥珀黄' },
  { value: 'sky', label: '天空蓝' },
  { value: 'violet', label: '星穹紫' },
  { value: 'orange', label: '活力橙' },
  { value: 'cyan', label: '青碧色' },
  { value: 'lime', label: '荧光绿' },
  { value: 'teal', label: '青绿色' },
  { value: 'blue', label: '品牌蓝' },
] as const

const { api } = useAdminApi()
const toast = useAdminToast()
const { communities: adminCommunities, labelOf, badgeOf, refreshSiteConfig } = useAdminCommunities()

const targets = ref<CollectTarget[]>([])
const error = ref<string | null>(null)
const busyId = ref<number | null>(null)

/* ---------- B 站登录态（扫码 / 粘贴 Cookie / 退出） ---------- */
const biliSession = ref<{ state: string; message: string; checked_at?: string; source?: string } | null>(null)
const checkingBili = ref(false)
const qrSession = ref<{ qrcode_key: string; qr_image: string; state: string; message: string } | null>(null)
const showCookieForm = ref(false)
const cookieText = ref('')
const cookieSaving = ref(false)
let qrTimer: ReturnType<typeof setInterval> | undefined

function stopQrPoll() {
  if (qrTimer) {
    clearInterval(qrTimer)
    qrTimer = undefined
  }
}
onBeforeUnmount(stopQrPoll)

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
    toast.err(exc?.data?.detail ?? exc?.message ?? 'B站登录状态检测失败')
  } finally {
    checkingBili.value = false
  }
}

async function startQrLogin() {
  stopQrPoll()
  showCookieForm.value = false
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

/* ---------- 分区管理（新建 / 编辑 / 启停 / 删除） ---------- */
const showCommunityPanel = ref(false)
const communityRows = ref<AdminCommunityRow[]>([])
const communityBusy = ref(false)
const editingCommunityKey = ref<string | null>(null)
const communityForm = reactive({
  key: '',
  label: '',
  en: '',
  color: 'blue' as SiteCommunityItem['color'],
  official_bili_mid: '',
  aliases: '',
})

async function loadCommunities() {
  try {
    const res = await api<{ items: AdminCommunityRow[] }>('/api/admin/communities')
    communityRows.value = res.items
  } catch { /* 静默回退 site-config */ }
}

function resetCommunityForm() {
  editingCommunityKey.value = null
  Object.assign(communityForm, { key: '', label: '', en: '', color: 'blue', official_bili_mid: '', aliases: '' })
}

function startEditCommunity(row: AdminCommunityRow) {
  showCommunityPanel.value = true
  editingCommunityKey.value = row.key
  Object.assign(communityForm, {
    key: row.key,
    label: row.label,
    en: row.en,
    color: row.color,
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
    domain: 'game',
    official_bili_mid: communityForm.official_bili_mid.trim() || null,
    aliases: communityForm.aliases.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    active: true,
  }
  try {
    if (editingCommunityKey.value) {
      await api(`/api/admin/communities/${editingCommunityKey.value}`, { method: 'PUT', body: payload })
      toast.ok(`分区「${payload.label}」已更新`)
    } else {
      await api('/api/admin/communities', { method: 'POST', body: { key, ...payload } })
      toast.ok(`自定义分区「${payload.label}」已创建，可直接为其添加采集源`)
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

async function batchCommunities(keys: string[], action: 'enable' | 'disable') {
  if (!keys.length || communityBusy.value) return
  communityBusy.value = true
  try {
    await api('/api/admin/communities/batch', { method: 'POST', body: { keys, action } })
    toast.ok(action === 'enable' ? '已批量显示所选分区' : '已隐藏无信源的空分区')
    await Promise.all([loadCommunities(), refreshSiteConfig()])
  } catch (exc: any) {
    toast.err(apiErrorDetail(exc, '批量调整分区失败'))
  } finally {
    communityBusy.value = false
  }
}

function hideEmptyCommunities() {
  const emptyKeys = communityRows.value.filter(c => c.active && c.target_count === 0).map(c => c.key)
  if (!emptyKeys.length) {
    toast.info('当前所有启用分区均已有采集目标')
    return
  }
  if (emptyKeys.length >= communityRows.value.filter(c => c.active).length) {
    emptyKeys.shift()
  }
  if (!emptyKeys.length) return
  void batchCommunities(emptyKeys, 'disable')
}

async function deleteCustomCommunity(row: AdminCommunityRow) {
  if (row.builtin || communityBusy.value) return
  const msg = row.target_count > 0
    ? `删除自定义分区「${row.label}」并同时移除其下 ${row.target_count} 个采集目标？已入库历史内容仍会保留。`
    : `确认删除自定义分区「${row.label}」？`
  if (!confirm(msg)) return
  communityBusy.value = true
  try {
    await api(`/api/admin/communities/${row.key}`, {
      method: 'DELETE',
      query: { purge_targets: row.target_count > 0 },
    })
    toast.ok(`分区「${row.label}」已删除`)
    if (communityFilter.value === row.key) communityFilter.value = ''
    await Promise.all([loadCommunities(), refreshSiteConfig(), refresh()])
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? '删除分区失败')
  } finally {
    communityBusy.value = false
  }
}

/* ---------- 一键导入预设信源 ---------- */
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

/* ---------- 采集目标表单与批量操作 ---------- */
const combos: Record<string, string[]> = {
  game_official: ['notices'],
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
  is_official: false,
})
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
    if (!confirm(`确认一键移除「${label}」分区下的全部 ${filteredTargets.value.length} 个采集目标？`)) return
  } else {
    if (!selectedIds.value.length) return
    if (action === 'delete' && !confirm(`确认一键移除选中的 ${selectedIds.value.length} 个采集目标？`)) return
  }
  batchBusy.value = true
  try {
    const body = opts?.community
      ? { community: opts.community, action }
      : { ids: selectedIds.value, action }
    const res = await api<{ affected: number }>('/api/admin/collect-targets/batch', { method: 'POST', body })
    const actionLabel = action === 'delete' ? '移除' : action === 'enable' ? '启用' : '停用'
    toast.ok(`已批量${actionLabel} ${res.affected} 个目标`)
    selectedIds.value = []
    await Promise.all([refresh(), loadCommunities(), refreshRuns()])
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '批量操作失败')
  } finally {
    batchBusy.value = false
  }
}

const availableTypes = computed(() => combos[form.platform] ?? [])
const supportsViews = computed(() => form.community !== 'ai' && form.platform === 'bilibili' && ['keyword_search', 'account_videos'].includes(form.target_type))
const defaultMinViews = () => (supportsViews.value && form.target_type === 'keyword_search' ? 10000 : 0)

const targetPlaceholder = computed(() => {
  if (form.platform === 'rss') return 'RSS / Atom 地址，如 https://openai.com/news/rss.xml'
  if (form.platform === 'youtube') return 'YouTube 24 位频道 ID（UC 开头）'
  if (form.platform === 'x') return '@账号 或 https://x.com/账号'
  if (form.platform === 'game_official') return '仅支持 genshin / endfield / nikke'
  if (form.platform === 'kuro_cn' || form.platform === 'kuro_global') return '官方公告标识（如 wuwa-official）'
  if (form.platform === 'bilibili' && form.target_type !== 'keyword_search') return 'B 站 UP 主数字 UID（如 1955897084）'
  return '搜索关键词（如 鸣潮 周边）'
})

watch(() => form.platform, () => {
  if (!editingId.value) {
    form.target_type = availableTypes.value[0] ?? 'keyword_search'
    form.interval_minutes = form.platform === 'x' ? 60 : 30
  }
})
watch(() => form.target_type, (next) => {
  if (next === 'keyword_search') form.is_official = false
})
watch([() => form.target_type, () => form.community, supportsViews], () => {
  if (!editingId.value) form.min_views = defaultMinViews()
})
watch([() => form.platform, () => form.community], () => {
  if (!editingId.value && form.platform === 'game_official' && ['genshin', 'endfield', 'nikke'].includes(form.community)) {
    form.target_value = form.community
  }
})

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
    return '未登录时动态接口常被风控，可在本页上方扫码登录提高成功率'
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

onMounted(async () => {
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
        is_official: form.target_type !== 'keyword_search' && form.is_official,
      },
    })
    editingId.value = null
    toast.ok(wasEditing ? '采集目标已保存' : '采集目标已创建')
    form.target_value = ''
    form.display_name = ''
    form.co_terms = ''
    form.min_views = defaultMinViews()
    form.tier = 'general'
    form.is_official = false
    await Promise.all([refresh(), loadCommunities()])
  } catch (exc: any) {
    formError.value = exc?.statusCode === 409 ? '该分区下已存在相同的采集目标' : `保存失败：${apiErrorDetail(exc, '请求失败')}`
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
    tier: target.tier, community: target.community, is_official: !!target.is_official,
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
  form.is_official = false
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
    selectedIds.value = selectedIds.value.filter(id => id !== target.id)
    toast.ok('目标已删除')
    await Promise.all([refresh(), loadCommunities(), refreshRuns()])
  } catch (exc: any) {
    toast.err(exc?.data?.detail ?? exc?.message ?? '删除失败，请重试')
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="wall-heading flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold">采集目标与分区定制</h1>
        <p class="mt-0.5 text-sm text-muted">
          支持自建任意分区、一键导入预设信源、批量启停/移除及后台 B 站扫码登录。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="rounded-md border border-line px-3 py-2 text-sm transition-colors hover:bg-chip"
          @click="showCommunityPanel = !showCommunityPanel"
        >
          {{ showCommunityPanel ? '收起分区管理' : `分区管理（${adminCommunities.length}）` }}
        </button>
        <button
          type="button"
          class="btn-primary px-3.5 py-2 text-sm"
          @click="openPresetPanel"
        >
          {{ showPresetPanel ? '收起预设导入' : '一键导入预设信源' }}
        </button>
      </div>
    </div>

    <!-- 分区管理面板 -->
    <section v-if="showCommunityPanel" class="panel space-y-4 p-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold">站点分区管理</h2>
          <p class="mt-0.5 text-xs text-muted">可随时隐藏不需要的内置分区，或自建全新的游戏/话题/行业分区（即时同步到前台顶栏）。</p>
        </div>
        <div class="flex flex-wrap gap-2 text-xs">
          <button type="button" class="rounded-md border border-line px-2.5 py-1.5 hover:bg-chip" :disabled="communityBusy" @click="hideEmptyCommunities">
            一键隐藏无信源分区
          </button>
          <button type="button" class="rounded-md border border-line px-2.5 py-1.5 hover:bg-chip" :disabled="communityBusy" @click="batchCommunities(communityRows.map(c => c.key), 'enable')">
            全部显示
          </button>
        </div>
      </div>

      <div class="flex flex-wrap gap-2">
        <div
          v-for="row in communityRows"
          :key="row.key"
          class="inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs"
          :class="row.active ? 'border-line bg-card text-ink' : 'border-line-soft bg-chip/50 text-faint opacity-70'"
        >
          <span class="font-medium">{{ row.label }}</span>
          <span class="mono text-[11px] text-faint">({{ row.target_count }})</span>
          <span v-if="!row.builtin" class="rounded bg-blue-500/10 px-1 py-0.5 text-[10px] text-blue-600 dark:text-blue-300">自建</span>
          <button type="button" class="ml-1 text-brand-ink hover:underline" :disabled="communityBusy" @click="toggleCommunityActive(row)">
            {{ row.active ? '隐藏' : '启用' }}
          </button>
          <template v-if="!row.builtin">
            <button type="button" class="text-muted hover:text-ink hover:underline" :disabled="communityBusy" @click="startEditCommunity(row)">编辑</button>
            <button type="button" class="text-red-500 hover:underline" :disabled="communityBusy" @click="deleteCustomCommunity(row)">删除</button>
          </template>
        </div>
      </div>

      <form class="grid gap-2.5 border-t border-line pt-4 sm:grid-cols-6" @submit.prevent="saveCommunity">
        <input
          v-model="communityForm.key"
          :disabled="!!editingCommunityKey"
          maxlength="16"
          placeholder="分区标识（1~16位，如 bluearchive）"
          aria-label="分区英文标识"
          class="input mono px-3 py-2 text-sm disabled:opacity-50"
        >
        <input
          v-model="communityForm.label"
          placeholder="分区名称（如 蔚蓝档案）"
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
        <select v-model="communityForm.color" aria-label="主题色" class="select px-3 py-2 text-sm">
          <option v-for="c in COLOR_OPTIONS" :key="c.value" :value="c.value">主题色：{{ c.label }}</option>
        </select>
        <input
          v-model="communityForm.official_bili_mid"
          placeholder="官方B站UID（可选）"
          aria-label="官方 B 站 UID"
          class="input mono px-3 py-2 text-sm"
        >
        <div class="flex items-center gap-2">
          <button type="submit" class="btn-primary flex-1 px-3 py-2 text-sm" :disabled="communityBusy || !communityForm.label.trim()">
            {{ editingCommunityKey ? '保存分区' : '新建分区' }}
          </button>
          <button v-if="editingCommunityKey" type="button" class="text-xs text-muted hover:underline" @click="resetCommunityForm">取消</button>
        </div>
      </form>
    </section>

    <!-- 一键导入预设信源面板 -->
    <section v-if="showPresetPanel" class="panel space-y-3 border-blue-500/30 bg-blue-500/5 p-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold">一键导入预设信源包</h2>
          <p class="mt-0.5 text-xs text-muted">勾选需要监控的分区即可幂等导入官方公告、视频、频道 RSS 与一手资讯源（X 源默认停用，避免未配抓取环境报错）。</p>
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
          :class="selectedPresets.includes(pack.community) ? 'border-blue-600 bg-blue-600 font-semibold text-white' : 'border-line bg-card text-ink-2 hover:text-ink'"
          @click="togglePresetKey(pack.community)"
        >
          <span>{{ pack.label }}</span>
          <span class="mono opacity-80">{{ pack.count }} 源</span>
        </button>
      </div>
      <div class="flex flex-wrap items-center gap-3 pt-1">
        <button
          type="button"
          class="btn-primary px-4 py-2 text-sm"
          :disabled="!selectedPresets.length || importingPresets"
          @click="importSelectedPresets"
        >
          {{ importingPresets ? '正在导入…' : `一键导入所选 ${selectedPresets.length} 个分区` }}
        </button>
        <span class="text-xs text-muted">已存在的同源目标会自动跳过，不会覆盖你改过的频率或门槛。</span>
      </div>
    </section>

    <!-- B 站登录态面板 -->
    <div v-if="biliSession" class="panel space-y-3 px-5 py-4" aria-live="polite">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="text-sm font-medium" :class="biliSession.state === 'logged_in' ? 'text-ok' : 'text-warn'">{{ biliSession.message }}</p>
          <p v-if="biliSession.checked_at" class="mono mt-1 text-xs text-muted">检测时间 {{ fmtTime(biliSession.checked_at) }}</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="btn-primary px-3 py-2 text-sm" :disabled="checkingBili" @click="startQrLogin">
            B 站扫码登录
          </button>
          <button type="button" class="rounded-md border border-line px-3 py-2 text-sm hover:bg-chip" @click="showCookieForm = !showCookieForm; closeQrLogin()">
            粘贴 Cookie
          </button>
          <button type="button" class="rounded-md border border-line px-3 py-2 text-sm hover:bg-chip" :disabled="checkingBili" @click="loadBiliSession(true)">
            {{ checkingBili ? '检测中…' : '检测登录态' }}
          </button>
          <button v-if="biliSession.state === 'logged_in'" type="button" class="rounded-md px-2.5 py-2 text-xs text-red-500 hover:bg-red-500/10" :disabled="checkingBili" @click="clearBiliLogin">
            退出
          </button>
        </div>
      </div>

      <div v-if="qrSession" class="flex flex-wrap items-center gap-4 rounded-xl border border-line bg-chip/40 p-4">
        <img :src="qrSession.qr_image" alt="B 站登录二维码" class="size-36 rounded-lg bg-white p-1.5">
        <div class="space-y-1.5 text-sm">
          <p class="font-medium text-ink">{{ qrSession.message }}</p>
          <p class="text-xs text-muted">打开哔哩哔哩手机 App 扫一扫确认登录，成功后凭据会自动加密写入数据库，重启容器不丢失。</p>
          <div class="flex gap-3 pt-1 text-xs">
            <button v-if="qrSession.state === 'expired'" type="button" class="text-brand-ink hover:underline" @click="startQrLogin">重新生成二维码</button>
            <button type="button" class="text-muted hover:underline" @click="closeQrLogin">关闭</button>
          </div>
        </div>
      </div>

      <form v-if="showCookieForm" class="space-y-2 rounded-xl border border-line bg-chip/40 p-4" @submit.prevent="saveManualCookie">
        <p class="text-xs text-muted">支持直接粘贴浏览器请求头中的 Cookie 字符串（需含 <code class="mono">SESSDATA</code>）或 JSON 对象：</p>
        <textarea
          v-model="cookieText"
          rows="2"
          placeholder="SESSDATA=...; bili_jct=...; DedeUserID=..."
          class="textarea mono w-full px-3 py-2 text-xs"
        />
        <div class="flex items-center gap-2">
          <button type="submit" class="btn-primary px-3.5 py-1.5 text-xs" :disabled="cookieSaving || !cookieText.trim()">
            {{ cookieSaving ? '核验保存中…' : '保存并核验 Cookie' }}
          </button>
          <button type="button" class="text-xs text-muted hover:underline" @click="showCookieForm = false">取消</button>
        </div>
      </form>
    </div>

    <!-- 新增 / 编辑单个目标 -->
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
        :placeholder="targetPlaceholder"
        :aria-label="form.platform === 'x' ? 'X 账号' : '目标值'"
        class="input px-3 py-2 text-sm"
      >
      <input v-model.number="form.interval_minutes" :aria-label="form.platform === 'x' ? '基准采集间隔（分钟）' : '采集间隔（分钟）'" :title="form.platform === 'x' ? '每轮随机浮动 ±25%，至少间隔 30 分钟' : ''" type="number" :min="form.platform === 'x' ? 30 : 5" max="1440" class="input mono px-3 py-2 text-sm">
      <select v-model="form.tier" class="select px-3 py-2 text-sm sm:col-span-2" title="核心关键词：商品/联名等特定内容，全量入库强及时；泛关键词：社区泛话题，走共现词与热度门槛">
        <option value="core">准入分级：核心信源（全量入库，强及时）</option>
        <option value="general">准入分级：泛关键词（走共现词与热度门槛）</option>
      </select>
      <select v-model="form.community" aria-label="所属分区" class="select px-3 py-2 text-sm" title="所属分区：决定内容与事件的归属">
        <option v-for="c in adminCommunities" :key="c.key" :value="c.key">分区：{{ c.label }}{{ c.active ? '' : '（已隐藏）' }}</option>
      </select>
      <input v-model="form.display_name" aria-label="显示名称" placeholder="显示名称（可选，如 OpenAI 官方博客）" class="input px-3 py-2 text-sm sm:col-span-2">
      <input v-model="form.co_terms" placeholder="共现词（逗号分隔，如 键盘,开箱,发货；留空不过滤；核心分级不生效）" class="input px-3 py-2 text-sm sm:col-span-2">
      <label class="text-xs text-muted">{{ supportsViews ? '最低播放量（0 不过滤）' : '此目标没有播放量门槛' }}<input v-model.number="form.min_views" type="number" min="0" :disabled="!supportsViews" class="input mono mt-1 w-full px-3 py-2 text-sm disabled:opacity-50"></label>
      <label class="flex items-center gap-2 self-end py-2 text-xs text-ink-2" :class="form.target_type === 'keyword_search' ? 'opacity-50' : 'cursor-pointer'">
        <input v-model="form.is_official" type="checkbox" class="checkbox" :disabled="form.target_type === 'keyword_search'">
        <span>标记为官方账号（入库内容标为「官方」）</span>
      </label>
      <button type="submit" class="btn-primary px-4 py-2" :disabled="creating || !form.target_value.trim()">
        {{ creating ? '保存中…' : editingId ? '保存修改' : '新增目标' }}
      </button>
      <button v-if="editingId" type="button" class="px-3 py-2 text-sm text-muted" :disabled="creating" @click="cancelEdit">取消编辑</button>
      <p v-if="formError" class="col-span-full text-sm text-red-600 dark:text-red-400">{{ formError }}</p>
    </form>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <!-- 分区筛选与批量操作栏 -->
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="text-sm font-semibold">目标列表（{{ filteredTargets.length }}）</h2>
        <template v-if="selectedIds.length">
          <span class="text-xs text-muted">已选 {{ selectedIds.length }} 项：</span>
          <button type="button" class="rounded bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-300" :disabled="batchBusy" @click="runBatchTargets('enable')">批量启用</button>
          <button type="button" class="rounded bg-chip px-2.5 py-1 text-xs text-ink-2 hover:bg-line" :disabled="batchBusy" @click="runBatchTargets('disable')">批量停用</button>
          <button type="button" class="rounded bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-600 hover:bg-red-500/20 dark:text-red-300" :disabled="batchBusy" @click="runBatchTargets('delete')">一键移除所选</button>
        </template>
        <button
          v-else-if="communityFilter && filteredTargets.length"
          type="button"
          class="rounded border border-red-500/30 px-2.5 py-1 text-xs text-red-600 hover:bg-red-500/10 dark:text-red-300"
          :disabled="batchBusy"
          @click="runBatchTargets('delete', { community: communityFilter })"
        >
          清空「{{ labelOf(communityFilter) }}」全部信源
        </button>
      </div>
      <div class="flex flex-wrap items-center gap-1">
        <button
          v-for="option in [['', '全部分区'], ...adminCommunities.map(c => [c.key, c.label] as const)]"
          :key="option[0]"
          type="button"
          class="rounded-full px-2.5 py-1 text-xs transition-colors"
          :class="communityFilter === option[0] ? 'bg-blue-600 text-white' : 'bg-chip text-muted hover:text-ink-2'"
          @click="communityFilter = option[0]"
        >
          {{ option[1] }}
        </button>
      </div>
    </div>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-[880px] text-sm">
        <thead>
          <tr class="border-b border-line-soft text-left text-xs text-muted dark:border-line">
            <th class="w-9 px-3 py-2.5">
              <input type="checkbox" class="checkbox" :checked="allFilteredSelected" :disabled="!filteredTargets.length" aria-label="全选当前列表" @change="toggleSelectAll">
            </th>
            <th class="px-3 py-2.5 font-medium">目标</th>
            <th class="px-2.5 py-2.5 font-medium">平台 · 类型</th>
            <th class="px-2.5 py-2.5 font-medium">分区</th>
            <th class="px-2.5 py-2.5 font-medium">分级</th>
            <th class="px-2.5 py-2.5 font-medium">播放门槛</th>
            <th class="px-2.5 py-2.5 font-medium">频率</th>
            <th class="px-2.5 py-2.5 font-medium">最近成功</th>
            <th class="px-2.5 py-2.5 font-medium">入库</th>
            <th class="px-3 py-2.5 font-medium">最近错误</th>
            <th class="px-3 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="target in filteredTargets" :key="target.id" class="border-b border-line-soft last:border-0" :class="target.enabled ? '' : 'opacity-60'">
            <td class="px-3 py-2.5">
              <input v-model="selectedIds" type="checkbox" :value="target.id" class="checkbox" :aria-label="`选择 ${target.display_name || target.target_value}`">
            </td>
            <td class="px-3 py-2.5">
              <span class="font-medium">{{ target.display_name && target.display_name !== target.target_value ? target.display_name : target.target_value }}</span>
              <span v-if="target.is_official" class="ml-1.5 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">官方</span>
              <span v-if="target.display_name && target.display_name !== target.target_value" class="mono ml-1.5 text-xs text-faint">{{ target.target_value }}</span>
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5 text-ink-2">
              {{ platformLabels[target.platform] ?? target.platform }}
              <span class="text-faint">·</span>
              {{ targetTypeLabels[target.target_type] ?? target.target_type }}
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5">
              <span class="chip whitespace-nowrap" :class="badgeOf(target.community)">
                {{ labelOf(target.community) }}
              </span>
            </td>
            <td class="whitespace-nowrap px-2.5 py-2.5">
              <button
                type="button"
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
            <td class="mono whitespace-nowrap px-2.5 py-2.5 text-xs text-muted">{{ fmtTime(target.last_success_at) }}</td>
            <td class="mono whitespace-nowrap px-2.5 py-2.5 text-xs text-muted">{{ target.last_items_new ?? '—' }}</td>
            <td class="max-w-52 truncate px-3 py-2.5 text-xs text-faint" :title="target.last_error ?? ''">
              {{ formatLastError(target.last_error) }}
            </td>
            <td class="whitespace-nowrap px-3 py-2.5 text-right">
              <button type="button" class="rounded px-1.5 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === target.id || creating" @click="edit(target)">编辑</button>
              <button type="button" class="rounded px-1.5 py-1 text-xs text-blue-600 hover:underline dark:text-blue-300" :disabled="busyId === target.id" @click="runNow(target)">立即采集</button>
              <button type="button" class="rounded px-1.5 py-1 text-xs text-muted hover:underline" :disabled="busyId === target.id" @click="toggle(target)">
                {{ target.enabled ? '停用' : '启用' }}
              </button>
              <button type="button" class="rounded px-1.5 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busyId === target.id" @click="remove(target)">删除</button>
            </td>
          </tr>
          <tr v-if="filteredTargets.length === 0">
            <td colspan="11" class="px-4 py-10 text-center text-sm text-faint">
              {{ targets.length === 0 ? '暂无采集目标，可点击上方「一键导入预设信源」或在表单中手动添加。' : '当前筛选分区下暂无目标。' }}
            </td>
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
