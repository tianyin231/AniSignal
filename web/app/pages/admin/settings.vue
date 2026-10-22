<script setup lang="ts">
import { BellRing, Mail, Megaphone, Save, Send } from 'lucide-vue-next'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '站点设置 · AniSignal 管理看板' })

const { api } = useAdminApi()
const busy = ref(false)
const busyWhat = ref('')
const startBusy = (name: string) => { busy.value = true; busyWhat.value = name }
const endBusy = () => { busy.value = false; busyWhat.value = '' }
const toast = useAdminToast()
const stamp = () => new Date().toLocaleTimeString('zh-CN', { hour12: false })
const error = ref('')
const notice = ref('')
// 三态加载：loading / ready / failed。只判 failed 是不够的——首屏 GET 还没回来时
// 表单里全是默认值，这时候点保存就是把真实配置覆盖成默认值。
const loaded = ref(false)
const loadFailed = ref(false)

// ---------- SMTP ----------
interface SmtpView {
  host: string
  port: number
  user: string
  password_set: boolean
  password_source: string
  from: string
  tls_mode: 'starttls' | 'tls' | 'none'
}
const smtp = reactive({ host: '', port: 587, user: '', password: '', from: '', tls_mode: 'starttls' as SmtpView['tls_mode'] })
const smtpInfo = ref<SmtpView | null>(null)
const testTo = ref('')
const testResult = ref('')

// ---------- OnePush ----------
interface OnePushView {
  enabled: boolean
  configured: boolean
  provider: string
  fields: string[]
}
const onepush = reactive({ enabled: false, config_yaml: '' })
const onepushInfo = ref<OnePushView | null>(null)
const onepushTestResult = ref('')

// ---------- AI（LLM） ----------
interface AiView { base_url: string; model: string; api_key_set: boolean }
const ai = reactive({ base_url: '', model: '', api_key: '' })
const aiTestResult = ref('')

// ---------- 公告 ----------
const announcement = reactive({ enabled: false, level: 'info', text: '', link: '' })

// ---------- 媒体可见性 ----------
interface MediaModesView { image: string; video: string }
const PLATFORM_MEDIA: { key: string; label: string }[] = [
  { key: 'bilibili', label: 'B站' },
  { key: 'youtube', label: 'YouTube' },
  { key: 'kuro_cn', label: '库街区·国服' },
  { key: 'kuro_global', label: '库街区·国际服' },
  { key: 'miyoushe', label: '米游社' },
  { key: 'x', label: 'X' },
]
const mediaVisibility = reactive({
  default: { image: 'show', video: 'show' },
  platforms: {} as Record<string, MediaModesView>,
})
const platformMode = (key: string, kind: 'image' | 'video'): string =>
  mediaVisibility.platforms[key]?.[kind] ?? 'inherit'
const setPlatformMode = (key: string, kind: 'image' | 'video', event: Event) => {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'inherit') {
    const cur = { ...mediaVisibility.platforms[key] }
    delete cur[kind]
    if (Object.keys(cur).length) mediaVisibility.platforms[key] = cur
    else delete mediaVisibility.platforms[key]
  } else {
    mediaVisibility.platforms[key] = { ...mediaVisibility.platforms[key], [kind]: value }
  }
}

async function saveMediaVisibility() {
  if (!guardLoaded()) return
  startBusy('saveMediaVisibility')
  error.value = notice.value = ''
  try {
    await api('/api/admin/settings/media-visibility', {
      method: 'PUT',
      body: JSON.parse(JSON.stringify(mediaVisibility)),
    })
    toast.ok('媒体可见性已保存，公开页即时生效')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败'
  } finally {
    endBusy()
  }
}

// ---------- 文案 ----------
const TEXT_LABELS: Record<string, string> = {
  footer_note: '页脚说明',
}
const texts = ref<Record<string, string>>({})

async function refresh() {
  loadFailed.value = false
  loaded.value = false
  error.value = ''
  let data: { smtp: SmtpView; onepush: OnePushView; ai: AiView; announcement: typeof announcement; texts: Record<string, string>; media_visibility?: any }
  try {
    data = await api('/api/admin/settings')
  } catch (exc: any) {
    // 加载失败就不要把默认值写进表单，更不要允许保存覆盖真实配置
    loadFailed.value = true
    error.value = exc?.data?.detail ?? '站点设置加载失败，请重试后再编辑'
    toast.err('站点设置加载失败，已禁止保存')
    return
  }
  smtpInfo.value = data.smtp
  smtp.host = data.smtp.host || ''
  smtp.port = data.smtp.port || 587
  smtp.user = data.smtp.user || ''
  smtp.from = data.smtp.from || ''
  smtp.tls_mode = data.smtp.tls_mode
  smtp.password = ''
  onepushInfo.value = data.onepush
  onepush.enabled = data.onepush.enabled
  onepush.config_yaml = ''
  ai.base_url = data.ai.base_url || ''
  ai.model = data.ai.model || ''
  ai.api_key = ''
  Object.assign(announcement, data.announcement)
  texts.value = { ...data.texts }
  const mv = data.media_visibility ?? { default: { image: 'show', video: 'show' }, platforms: {} }
  mediaVisibility.default = { image: mv.default?.image ?? 'show', video: mv.default?.video ?? 'show' }
  mediaVisibility.platforms = mv.platforms ?? {}
  loaded.value = true
}

/** 所有会写服务端配置的入口先过这道闸：没加载成功就一律拒绝保存。 */
function guardLoaded(): boolean {
  if (loadFailed.value) {
    error.value = '站点设置未加载成功，请先点「重试加载」'
    return false
  }
  if (!loaded.value) {
    error.value = '站点设置还在加载中，请稍候'
    return false
  }
  return true
}

async function saveOnepush() {
  if (!guardLoaded()) return
  startBusy('saveOnepush')
  error.value = notice.value = ''
  try {
    onepushInfo.value = await api<OnePushView>('/api/admin/settings/onepush', {
      method: 'PUT',
      body: onepush,
    })
    onepush.config_yaml = ''
    toast.ok('OnePush 配置已保存')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败'
    toast.err('OnePush 保存失败，请检查 YAML')
  } finally {
    endBusy()
  }
}

async function testOnepush() {
  if (!guardLoaded()) return
  if (busy.value) return
  startBusy('testOnepush')
  onepushTestResult.value = ''
  try {
    const result = await api<{ ok: boolean; message: string }>('/api/admin/settings/onepush/test', {
      method: 'POST',
    })
    onepushTestResult.value = `${stamp()} · ${result.message}`
    result.ok ? toast.ok('OnePush 测试消息已发出') : toast.err('OnePush 测试失败')
  } catch (exc: any) {
    onepushTestResult.value = `${stamp()} · ${exc?.data?.detail ?? '发送失败'}`
    toast.err('OnePush 测试请求失败')
  } finally {
    endBusy()
  }
}
onMounted(refresh)

async function saveSmtp() {
  if (!guardLoaded()) return
  startBusy('saveSmtp')
  error.value = notice.value = ''
  try {
    smtpInfo.value = await api<SmtpView>('/api/admin/settings/smtp', { method: 'PUT', body: smtp })
    smtp.password = ''
    toast.ok('SMTP 配置已保存')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败'
    toast.err('保存失败，请检查输入')
  } finally {
    endBusy()
  }
}

async function sendTest() {
  if (!guardLoaded()) return
  if (!testTo.value.trim() || busy.value) return
  startBusy('sendTest')
  testResult.value = ''
  try {
    const result = await api<{ sent: boolean; message: string }>('/api/admin/settings/smtp/test', {
      method: 'POST',
      body: { to: testTo.value.trim() },
    })
    testResult.value = `${stamp()} · ${result.message}`
    result.sent ? toast.ok('测试邮件已发出') : toast.err('测试邮件未发出，详见结果与日志')
  } catch (exc: any) {
    testResult.value = `${stamp()} · ${exc?.data?.detail ?? '发送失败'}`
    toast.err('测试发送失败')
  } finally {
    endBusy()
  }
}

async function saveAi() {
  if (!guardLoaded()) return
  startBusy('saveAi')
  error.value = notice.value = ''
  try {
    await api('/api/admin/settings/ai', { method: 'PUT', body: ai })
    ai.api_key = ''
    toast.ok('LLM 配置已保存')
    await refresh()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败'
  } finally {
    endBusy()
  }
}

async function testAi() {
  if (!guardLoaded()) return
  if (busy.value) return
  startBusy('testAi')
  aiTestResult.value = ''
  try {
    const r = await api<{ ok: boolean; message: string }>('/api/admin/settings/ai/test', { method: 'POST' })
    aiTestResult.value = `${stamp()} · ${r.message}`
    r.ok ? toast.ok('LLM 连通正常') : toast.err('LLM 连通失败')
  } catch (exc: any) {
    aiTestResult.value = `${stamp()} · ${exc?.data?.detail ?? '测试失败'}`
    toast.err('LLM 测试请求失败')
  } finally {
    endBusy()
  }
}

async function summarizeAll() {
  if (!guardLoaded()) return
  if (!confirm('为缺少摘要或使用降级模板的事件补 AI 摘要？人工编辑和锁定的事件会保留。')) return
  startBusy('summarizeAll')
  try {
    const r = await api<{ candidates: number; queued: number; remaining: number }>('/api/admin/settings/ai/summarize-all', { method: 'POST' })
    toast.info(r.queued ? `已新增 ${r.queued} 条摘要任务，后台处理中；当前待补 ${r.remaining} 条（含队列中）` : r.remaining ? '本批摘要任务已在队列中，请稍后查看 AI 调用记录' : '无需补充摘要')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '执行失败'
  } finally {
    endBusy()
  }
}

async function saveAnnouncement() {
  if (!guardLoaded()) return
  startBusy('saveAnnouncement')
  error.value = notice.value = ''
  try {
    await api('/api/admin/settings/announcement', { method: 'PUT', body: announcement })
    toast.ok('公告已保存' + (announcement.enabled ? '并启用' : '（当前关闭）'))
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败'
  } finally {
    endBusy()
  }
}

async function saveTexts() {
  if (!guardLoaded()) return
  startBusy('saveTexts')
  error.value = notice.value = ''
  try {
    texts.value = await api('/api/admin/settings/texts', { method: 'PUT', body: texts.value })
    toast.ok('文案已保存并全站生效')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败'
  } finally {
    endBusy()
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="wall-heading">
      <h1 class="text-2xl font-bold">站点设置</h1>
      <p class="mt-0.5 text-sm text-muted">
        OnePush、SMTP 发信、全服公告与各页面文案区域；保存即生效，无需重启。
      </p>
    </div>

    <p v-if="notice" class="panel px-4 py-3 text-sm text-emerald-600 dark:text-emerald-400">{{ notice }}</p>
    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    <div v-if="loadFailed" class="panel flex flex-wrap items-center justify-between gap-3 border-red-300/60 px-4 py-3 text-sm text-red-600 dark:border-red-500/40 dark:text-red-400">
      <span>站点设置没有加载成功，为避免用默认值覆盖线上配置，保存已禁用。</span>
      <button class="rounded-md border border-red-300/60 px-3 py-1.5 text-xs dark:border-red-500/40" :disabled="busy" @click="refresh">重试加载</button>
    </div>

    <!-- SMTP -->
    <section class="panel p-5">
      <h2 class="flex items-center gap-2 text-sm font-semibold"><Mail :size="15" /> 邮件发送（SMTP）</h2>
      <p class="mt-1 text-xs leading-relaxed text-muted">
        未配置时验证码只落库：管理端「验证码」列表可查注册码，密码重置码不再回传（等于账号接管凭证），所以找回密码需要配置 SMTP。
        <template v-if="smtpInfo?.password_set">
          当前密码：已设置（来源 {{ smtpInfo.password_source === 'db' ? '本页' : '环境变量' }}），留空保存则保留。
        </template>
      </p>
      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <label for="smtp-host" class="block text-xs font-medium text-muted">SMTP 服务器</label>
          <input id="smtp-host" v-model="smtp.host" placeholder="如 smtp.qq.com" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label for="smtp-port" class="block text-xs font-medium text-muted">端口</label>
          <input id="smtp-port" v-model.number="smtp.port" type="number" class="input mono mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label for="smtp-user" class="block text-xs font-medium text-muted">账号</label>
          <input id="smtp-user" v-model="smtp.user" autocomplete="username" placeholder="xxx@qq.com" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label for="smtp-password" class="block text-xs font-medium text-muted">密码 / 授权码</label>
          <input id="smtp-password" v-model="smtp.password" type="password" autocomplete="new-password" placeholder="留空 = 保留已存密码" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div class="sm:col-span-2">
          <label for="smtp-from" class="block text-xs font-medium text-muted">发件人（From）</label>
          <input id="smtp-from" v-model="smtp.from" placeholder="AniSignal <noreply@example.com>" class="mt-1 w-full rounded-md border border-line bg-white/70 px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:bg-chip/60">
        </div>
        <div class="sm:col-span-2">
          <label for="smtp-tls-mode" class="block text-xs font-medium text-muted">连接加密</label>
          <select id="smtp-tls-mode" v-model="smtp.tls_mode" class="select mt-1 w-full px-3 py-2 text-sm sm:w-72">
            <option value="starttls">STARTTLS（通常使用 587）</option>
            <option value="tls">SSL/TLS（通常使用 465）</option>
            <option value="none">不加密</option>
          </select>
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-2">
        <button class="btn-primary inline-flex min-h-11 cursor-pointer items-center gap-1.5 px-4 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" :disabled="busy || !loaded || loadFailed" @click="saveSmtp">
          <Save :size="14" /> {{ busyWhat === 'saveSmtp' ? '保存中…' : '保存 SMTP' }}
        </button>
        <label for="smtp-test-to" class="sr-only">测试收件邮箱</label>
        <input id="smtp-test-to" v-model="testTo" type="email" placeholder="测试收件邮箱" class="input min-h-11 w-52 px-3 py-2 text-sm">
        <button class="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-md border border-line px-4 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" :disabled="busy || !loaded || loadFailed || !testTo.trim()" @click="sendTest">
          <Send :size="14" /> {{ busyWhat === 'sendTest' ? '发送中…' : '发送测试邮件' }}
        </button>
        <span v-if="testResult" class="text-xs" :class="testResult.includes('失败') ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'">{{ testResult }}</span>
      </div>
    </section>
    <section class="panel p-5">
      <div class="flex items-center justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold">媒体可见性</h2>
          <p class="mt-0.5 text-xs text-muted">
            按平台控制封面图片与来源链接的展示；全站默认对未单独设置的平台生效，保存即生效。
          </p>
        </div>
        <button class="btn-primary px-4 py-2 text-sm" :disabled="busy || !loaded || loadFailed" @click="saveMediaVisibility">
          <Save :size="14" class="mr-1 inline" />{{ busyWhat === 'saveMediaVisibility' ? '保存中…' : '保存媒体可见性' }}
        </button>
      </div>
      <table class="mt-4 w-full text-sm">
        <thead>
          <tr class="border-b border-line-soft text-left text-xs text-muted dark:border-line">
            <th class="px-3 py-2 font-medium">范围</th>
            <th class="px-3 py-2 font-medium">图片</th>
            <th class="px-3 py-2 font-medium">视频/来源链接</th>
          </tr>
        </thead>
        <tbody>
          <tr class="border-b border-line-soft dark:border-line">
            <td class="px-3 py-2 font-medium text-ink-2">全站默认</td>
            <td class="px-3 py-2">
              <select v-model="mediaVisibility.default.image" class="select px-2 py-1.5 text-xs">
                <option value="show">显示图片</option>
                <option value="link">仅展示链接</option>
                <option value="hide">不显示</option>
              </select>
            </td>
            <td class="px-3 py-2">
              <select v-model="mediaVisibility.default.video" class="select px-2 py-1.5 text-xs">
                <option value="show">显示视频来源</option>
                <option value="link">仅展示链接</option>
                <option value="hide">不显示</option>
              </select>
            </td>
          </tr>
          <tr v-for="p in PLATFORM_MEDIA" :key="p.key" class="border-b border-line-soft last:border-0 dark:border-line">
            <td class="px-3 py-2 text-ink-2">{{ p.label }}</td>
            <td class="px-3 py-2">
              <select
                :value="platformMode(p.key, 'image')"
                class="select px-2 py-1.5 text-xs"
                @change="setPlatformMode(p.key, 'image', $event)"
              >
                <option value="inherit">跟随全站默认</option>
                <option value="show">显示图片</option>
                <option value="link">仅展示链接</option>
                <option value="hide">不显示</option>
              </select>
            </td>
            <td class="px-3 py-2">
              <select
                :value="platformMode(p.key, 'video')"
                class="select px-2 py-1.5 text-xs"
                @change="setPlatformMode(p.key, 'video', $event)"
              >
                <option value="inherit">跟随全站默认</option>
                <option value="show">显示来源</option>
                <option value="link">仅展示链接</option>
                <option value="hide">不显示</option>
              </select>
            </td>
          </tr>
        </tbody>
      </table>
    </section>


    <!-- OnePush -->
    <section class="panel p-5">
      <h2 class="flex items-center gap-2 text-sm font-semibold"><BellRing :size="15" /> OnePush 全站推送</h2>
      <p class="mt-1 text-xs leading-relaxed text-muted">
        全站共用一个管理员推送通道，适合 Bark、PushPlus、Server 酱、企业微信等。
        参数按 OnePush YAML 格式填写；保存后不会回显 token、key 等敏感值。
        <a href="https://github.com/LmeSzinc/AzurLaneAutoScript/wiki/Onepush-configuration-%5BCN%5D" target="_blank" rel="noopener noreferrer" class="link rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">查看配置说明</a>
      </p>
      <div v-if="onepushInfo?.configured" class="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-ink-2">
        当前 provider：<span class="mono font-medium text-blue-700 dark:text-blue-300">{{ onepushInfo.provider }}</span>
        <template v-if="onepushInfo.fields.length"> · 已保存字段：{{ onepushInfo.fields.join('、') }}</template>
      </div>
      <label class="mt-4 flex min-h-11 cursor-pointer items-center gap-2 text-sm">
        <input v-model="onepush.enabled" type="checkbox" class="checkbox">
        启用 OnePush 自动发送
      </label>
      <div class="mt-3">
        <label for="onepush-config" class="block text-xs font-medium text-muted">OnePush YAML 配置</label>
        <textarea
          id="onepush-config"
          v-model="onepush.config_yaml"
          rows="5"
          spellcheck="false"
          placeholder="provider: bark&#10;key: YOUR_BARK_KEY"
          class="textarea mono mt-1 w-full px-3 py-2 text-sm leading-relaxed"
        />
        <p class="mt-1.5 text-xs text-muted">已配置时留空保存会保留原参数；关闭开关只停止自动发送，仍可执行测试。</p>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-2">
        <button class="btn-primary inline-flex min-h-11 cursor-pointer items-center gap-1.5 px-4 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" :disabled="busy || !loaded || loadFailed" @click="saveOnepush">
          <Save :size="14" /> {{ busyWhat === 'saveOnepush' ? '保存中…' : '保存 OnePush' }}
        </button>
        <button class="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-md border border-line px-4 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" :disabled="busy || !loaded || loadFailed || !onepushInfo?.configured" @click="testOnepush">
          <Send :size="14" /> {{ busyWhat === 'testOnepush' ? '发送中…' : '发送测试推送' }}
        </button>
        <span v-if="onepushTestResult" class="text-xs" :class="onepushTestResult.includes('失败') || onepushTestResult.includes('未配置') ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'">{{ onepushTestResult }}</span>
      </div>
    </section>

    <!-- AI / LLM -->
    <section class="panel p-5">
      <h2 class="text-sm font-semibold">AI 层（LLM）</h2>
      <p class="mt-1 text-xs leading-relaxed text-muted">
        OpenAI 兼容接口。配置后自动为事件生成中文摘要与关键事实、翻译国际服公告；
        未配置时这些任务安全跳过，不影响采集。
      </p>
      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <div class="sm:col-span-2">
          <label class="block text-xs font-medium text-muted">Base URL（如 https://api.deepseek.com/v1）</label>
          <input v-model="ai.base_url" placeholder="https://api.example.com/v1" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-muted">模型名（如 deepseek-chat）</label>
          <input v-model="ai.model" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-muted">API Key（留空 = 保留已存）</label>
          <input v-model="ai.api_key" type="password" placeholder="sk-…" class="input mt-1 w-full px-3 py-2 text-sm">
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-2">
        <button class="btn-primary px-4 py-2" :disabled="busy || !loaded || loadFailed" @click="saveAi">{{ busyWhat === 'saveAi' ? '保存中…' : '保存 LLM 配置' }}</button>
        <button class="rounded-md border border-line px-4 py-2 text-sm" :disabled="busy || !loaded || loadFailed" @click="testAi">{{ busyWhat === 'testAi' ? '测试中…' : '连通测试' }}</button>
        <button class="rounded-md border border-line px-4 py-2 text-sm" :disabled="busy || !loaded || loadFailed" @click="summarizeAll">{{ busyWhat === 'summarizeAll' ? '执行中…' : '批量补事件摘要' }}</button>
        <span v-if="aiTestResult" class="text-xs" :class="aiTestResult.includes('失败') || aiTestResult.includes('未配置') ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'">{{ aiTestResult }}</span>
      </div>
    </section>

    <!-- 全服公告 -->
    <section class="panel p-5">
      <h2 class="flex items-center gap-2 text-sm font-semibold"><Megaphone :size="14" /> 全服公告</h2>
      <p class="mt-1 text-xs leading-relaxed text-muted">
        启用后全站顶部显示横幅（访客可关闭）；文本为空时自动不显示。
      </p>
      <div class="mt-4 space-y-3">
        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm">
            <input v-model="announcement.enabled" type="checkbox" class="checkbox"> 启用公告
          </label>
          <div class="flex items-center gap-2 text-sm">
            <span class="text-xs text-muted">级别</span>
            <select v-model="announcement.level" class="select px-2 py-1.5 text-sm">
              <option value="info">通知（蓝）</option>
              <option value="warning">警示（琥珀）</option>
            </select>
          </div>
          <input v-model="announcement.link" placeholder="跳转链接（可选，如 /about）" class="input w-56 px-3 py-2 text-sm">
        </div>
        <textarea v-model="announcement.text" rows="2" maxlength="500" placeholder="公告内容，如：今晚 22:00-23:00 系统维护，期间采集可能暂停。"
          class="textarea w-full px-3 py-2 text-sm" />
      </div>
      <button class="btn-primary mt-3 px-4 py-2" :disabled="busy || !loaded || loadFailed" @click="saveAnnouncement">{{ busyWhat === 'saveAnnouncement' ? '保存中…' : '保存公告' }}</button>
    </section>

    <!-- 文案区域 -->
    <section class="panel p-5">
      <h2 class="text-sm font-semibold">页面文案区域</h2>
      <div class="mt-4 space-y-3">
        <div v-for="(label, key) in TEXT_LABELS" :key="key">
          <label class="block text-xs font-medium text-muted">{{ label }}（{{ key }}）</label>
          <textarea v-model="texts[key]" rows="2" class="textarea mt-1 w-full px-3 py-2 text-sm" />
        </div>
      </div>
      <button class="btn-primary mt-4 px-4 py-2" :disabled="busy || !loaded || loadFailed" @click="saveTexts">{{ busyWhat === 'saveTexts' ? '保存中…' : '保存文案' }}</button>
    </section>
  </div>
</template>
