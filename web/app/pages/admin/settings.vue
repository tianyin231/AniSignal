<script setup lang="ts">
import { Megaphone, Save, Send } from 'lucide-vue-next'

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

// ---------- SMTP ----------
interface SmtpView {
  host: string
  port: number
  user: string
  password_set: boolean
  password_source: string
  from: string
  starttls: boolean
}
const smtp = reactive({ host: '', port: 587, user: '', password: '', from: '', starttls: true })
const smtpInfo = ref<SmtpView | null>(null)
const testTo = ref('')
const testResult = ref('')

// ---------- AI（LLM） ----------
interface AiView { base_url: string; model: string; api_key_set: boolean }
const ai = reactive({ base_url: '', model: '', api_key: '' })
const aiTestResult = ref('')

// ---------- 公告 ----------
const announcement = reactive({ enabled: false, level: 'info', text: '', link: '' })

// ---------- 文案 ----------
const TEXT_LABELS: Record<string, string> = {
  footer_note: '页脚说明',
  home_intro: '首页介绍行',
  official_note: '官方动态页提示',
  events_note: '事件库页提示',
  sources_note: '来源健康页说明',
  watch_intro: '订阅中心页说明',
}
const texts = ref<Record<string, string>>({})

async function refresh() {
  const data = await api<{ smtp: SmtpView; announcement: typeof announcement; texts: Record<string, string> }>(
    '/api/admin/settings',
  )
  smtpInfo.value = data.smtp
  smtp.host = data.smtp.host || ''
  smtp.port = data.smtp.port || 587
  smtp.user = data.smtp.user || ''
  smtp.from = data.smtp.from || ''
  smtp.starttls = data.smtp.starttls
  smtp.password = ''
  Object.assign(announcement, data.announcement)
  texts.value = { ...data.texts }
}
onMounted(refresh)

async function saveSmtp() {
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
  if (!confirm('为缺少摘要的事件批量生成 AI 摘要？')) return
  startBusy('summarizeAll')
  try {
    const r = await api<{ attempted: number; ok: number; skipped: number }>('/api/admin/settings/ai/summarize-all', { method: 'POST' })
    toast.ok(`AI 摘要完成：处理 ${r.attempted}，成功 ${r.ok}，跳过 ${r.skipped}`)
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '执行失败'
  } finally {
    endBusy()
  }
}

async function saveAnnouncement() {
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
    <div>
      <h1 class="text-lg font-semibold">站点设置</h1>
      <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
        SMTP 发信、全服公告与各页面文案区域；保存即生效，无需重启。
      </p>
    </div>

    <p v-if="notice" class="panel px-4 py-3 text-sm text-emerald-600 dark:text-emerald-400">{{ notice }}</p>
    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <!-- SMTP -->
    <section class="panel p-5">
      <h2 class="flex items-center gap-2 text-sm font-semibold">邮件发送（SMTP）</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        未配置时注册/找回验证码只存库（管理端可查）；配置后验证码真实发送到用户邮箱。
        <template v-if="smtpInfo?.password_set">
          当前密码：已设置（来源 {{ smtpInfo.password_source === 'db' ? '本页' : '环境变量' }}），留空保存则保留。
        </template>
      </p>
      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">SMTP 服务器</label>
          <input v-model="smtp.host" placeholder="如 smtp.qq.com" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">端口（587 = STARTTLS）</label>
          <input v-model.number="smtp.port" type="number" class="mono mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">账号</label>
          <input v-model="smtp.user" placeholder="xxx@qq.com" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">密码 / 授权码</label>
          <input v-model="smtp.password" type="password" placeholder="留空 = 保留已存密码" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <div class="sm:col-span-2">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">发件人（From）</label>
          <input v-model="smtp.from" placeholder="AniSignal <noreply@example.com>" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
      </div>
      <label class="mt-3 flex items-center gap-2 text-sm">
        <input v-model="smtp.starttls" type="checkbox" class="rounded"> 启用 STARTTLS
      </label>
      <div class="mt-4 flex flex-wrap items-center gap-2">
        <button class="btn-primary inline-flex items-center gap-1.5 px-4 py-2" :disabled="busy" @click="saveSmtp">
          <Save :size="14" /> {{ busyWhat === 'saveSmtp' ? '保存中…' : '保存 SMTP' }}
        </button>
        <input v-model="testTo" type="email" placeholder="测试收件邮箱" class="w-52 rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        <button class="inline-flex items-center gap-1.5 rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" :disabled="busy || !testTo.trim()" @click="sendTest">
          <Send :size="14" /> {{ busyWhat === 'sendTest' ? '发送中…' : '发送测试邮件' }}
        </button>
        <span v-if="testResult" class="text-xs" :class="testResult.includes('失败') ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'">{{ testResult }}</span>
      </div>
    </section>

    <!-- AI / LLM -->
    <section class="panel p-5">
      <h2 class="text-sm font-semibold">AI 层（LLM）</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        OpenAI 兼容接口。配置后自动为事件生成中文摘要与关键事实、翻译国际服公告；
        未配置时这些任务安全跳过，不影响采集。
      </p>
      <div class="mt-4 grid gap-3 sm:grid-cols-2">
        <div class="sm:col-span-2">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">Base URL（如 https://api.deepseek.com/v1）</label>
          <input v-model="ai.base_url" placeholder="https://api.example.com/v1" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">模型名（如 deepseek-chat）</label>
          <input v-model="ai.model" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">API Key（留空 = 保留已存）</label>
          <input v-model="ai.api_key" type="password" placeholder="sk-…" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-2">
        <button class="btn-primary px-4 py-2" :disabled="busy" @click="saveAi">{{ busyWhat === 'saveAi' ? '保存中…' : '保存 LLM 配置' }}</button>
        <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" :disabled="busy" @click="testAi">{{ busyWhat === 'testAi' ? '测试中…' : '连通测试' }}</button>
        <button class="rounded-md border border-slate-900/10 px-4 py-2 text-sm dark:border-white/15" :disabled="busy" @click="summarizeAll">{{ busyWhat === 'summarizeAll' ? '执行中…' : '批量补事件摘要' }}</button>
        <span v-if="aiTestResult" class="text-xs" :class="aiTestResult.includes('失败') || aiTestResult.includes('未配置') ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'">{{ aiTestResult }}</span>
      </div>
    </section>

    <!-- 全服公告 -->
    <section class="panel p-5">
      <h2 class="flex items-center gap-2 text-sm font-semibold"><Megaphone :size="14" /> 全服公告</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        启用后全站顶部显示横幅（访客可关闭）；文本为空时自动不显示。
      </p>
      <div class="mt-4 space-y-3">
        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm">
            <input v-model="announcement.enabled" type="checkbox" class="rounded"> 启用公告
          </label>
          <div class="flex items-center gap-2 text-sm">
            <span class="text-xs text-slate-500 dark:text-zinc-400">级别</span>
            <select v-model="announcement.level" class="rounded-md border border-slate-900/10 bg-white/70 px-2 py-1.5 text-sm dark:border-white/15 dark:bg-white/5">
              <option value="info">通知（蓝）</option>
              <option value="warning">警示（琥珀）</option>
            </select>
          </div>
          <input v-model="announcement.link" placeholder="跳转链接（可选，如 /about）" class="w-56 rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5">
        </div>
        <textarea v-model="announcement.text" rows="2" maxlength="500" placeholder="公告内容，如：今晚 22:00-23:00 系统维护，期间采集可能暂停。"
          class="w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" />
      </div>
      <button class="btn-primary mt-3 px-4 py-2" :disabled="busy" @click="saveAnnouncement">{{ busyWhat === 'saveAnnouncement' ? '保存中…' : '保存公告' }}</button>
    </section>

    <!-- 文案区域 -->
    <section class="panel p-5">
      <h2 class="text-sm font-semibold">页面文案区域</h2>
      <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        各页面展示说明文字，留空恢复默认；保存后全站立即生效。
      </p>
      <div class="mt-4 space-y-3">
        <div v-for="(label, key) in TEXT_LABELS" :key="key">
          <label class="block text-xs font-medium text-slate-500 dark:text-zinc-400">{{ label }}（{{ key }}）</label>
          <textarea v-model="texts[key]" rows="2" class="mt-1 w-full rounded-md border border-slate-900/10 bg-white/70 px-3 py-2 text-sm dark:border-white/15 dark:bg-white/5" />
        </div>
      </div>
      <button class="btn-primary mt-4 px-4 py-2" :disabled="busy" @click="saveTexts">{{ busyWhat === 'saveTexts' ? '保存中…' : '保存文案' }}</button>
    </section>
  </div>
</template>
