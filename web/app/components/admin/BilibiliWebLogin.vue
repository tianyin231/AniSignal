<script setup lang="ts">
import { apiErrorDetail } from '~/utils/api-errors'
import { verifyGeetest, type CaptchaTicket } from '~/utils/geetest'

const props = defineProps<{ mode: 'password' | 'sms' }>()
const emit = defineEmits<{ login: [session: BiliSession]; close: [] }>()
interface BiliSession { state: string; message: string; checked_at?: string; source?: string }
interface LoginResult { state: string; message: string; session?: BiliSession; verification_url?: string | null }

const { api } = useAdminApi()
const toast = useAdminToast()
const username = ref('')
const password = ref('')
const phone = ref('')
const cid = ref(1)
const code = ref('')
const captchaKey = ref('')
const countries = ref([{ id: 1, name: '中国大陆', code: '86' }])
const busy = ref('')
const error = ref('')
const verificationUrl = ref('')
const remaining = ref(0)
let countdown: ReturnType<typeof setInterval> | undefined
let controller: AbortController | undefined
let disposed = false

onBeforeUnmount(() => {
  disposed = true
  controller?.abort()
  clearInterval(countdown)
  password.value = code.value = captchaKey.value = ''
})

watch([phone, cid], () => { code.value = captchaKey.value = ''; verificationUrl.value = '' })
watch(() => props.mode, async (mode) => {
  controller?.abort()
  password.value = code.value = captchaKey.value = ''
  error.value = verificationUrl.value = ''
  if (mode === 'sms' && countries.value.length === 1) {
    try {
      countries.value = await api('/api/admin/bilibili-session/countries')
    } catch { /* 地区接口暂不可用时保留中国大陆 */ }
  }
}, { immediate: true })

async function captcha(signal: AbortSignal) {
  const ticket = await api<CaptchaTicket>('/api/admin/bilibili-session/captcha', { method: 'POST', signal })
  return await verifyGeetest(ticket, signal)
}

function finish(result: LoginResult) {
  if (disposed) return
  if (result.state === 'saved' && result.session) {
    toast.ok(result.message)
    emit('login', result.session)
  } else {
    error.value = result.message
    verificationUrl.value = result.verification_url || ''
    toast.info(result.message)
  }
}

async function submitPassword() {
  if (busy.value || !username.value.trim() || !password.value) return
  busy.value = 'password'
  error.value = verificationUrl.value = ''
  controller = new AbortController()
  try {
    const verification = await captcha(controller.signal)
    finish(await api<LoginResult>('/api/admin/bilibili-session/login/password', {
      method: 'POST', signal: controller.signal,
      body: { username: username.value.trim(), password: password.value, ...verification },
    }))
  } catch (exc) {
    if (!disposed && !controller.signal.aborted) {
      error.value = apiErrorDetail(exc, exc instanceof Error ? exc.message : '账号密码登录失败')
      toast.err(error.value)
    }
  } finally { password.value = ''; busy.value = '' }
}

async function sendCode() {
  if (busy.value || remaining.value || !/^[0-9]{5,20}$/.test(phone.value.trim())) return
  busy.value = 'send'
  error.value = verificationUrl.value = ''
  controller = new AbortController()
  try {
    const verification = await captcha(controller.signal)
    const result = await api<{ captcha_key: string; retry_after: number; message: string }>('/api/admin/bilibili-session/sms/send', {
      method: 'POST', signal: controller.signal,
      body: { cid: cid.value, phone: phone.value.trim(), ...verification },
    })
    if (disposed) return
    captchaKey.value = result.captcha_key
    remaining.value = result.retry_after
    clearInterval(countdown)
    countdown = setInterval(() => { if (--remaining.value <= 0) clearInterval(countdown) }, 1000)
    toast.ok(result.message)
  } catch (exc) {
    if (!disposed && !controller.signal.aborted) {
      error.value = apiErrorDetail(exc, exc instanceof Error ? exc.message : '短信验证码发送失败')
      toast.err(error.value)
    }
  } finally { busy.value = '' }
}

async function submitSms() {
  if (busy.value || !captchaKey.value || !/^[0-9]{4,8}$/.test(code.value.trim())) return
  busy.value = 'sms'
  error.value = verificationUrl.value = ''
  controller = new AbortController()
  try {
    finish(await api<LoginResult>('/api/admin/bilibili-session/login/sms', {
      method: 'POST', signal: controller.signal,
      body: { cid: cid.value, phone: phone.value.trim(), code: code.value.trim(), captcha_key: captchaKey.value },
    }))
  } catch (exc) {
    if (!disposed && !controller.signal.aborted) {
      error.value = apiErrorDetail(exc, exc instanceof Error ? exc.message : '短信验证码登录失败')
      toast.err(error.value)
    }
  } finally { code.value = ''; busy.value = '' }
}
</script>

<template>
  <div class="rounded-xl border border-line bg-chip/40 p-4">
    <div class="mb-4 flex items-center justify-between gap-3">
      <h3 class="text-sm font-medium">{{ mode === 'password' ? 'B 站账号密码登录' : 'B 站短信验证码登录' }}</h3>
      <button type="button" class="text-xs text-muted hover:text-ink" @click="emit('close')">关闭</button>
    </div>
    <form v-if="mode === 'password'" class="grid max-w-xl gap-3 sm:grid-cols-2" @submit.prevent="submitPassword">
      <label class="text-xs text-ink-2" for="bili-username">手机号或邮箱
        <input id="bili-username" v-model="username" type="text" autocomplete="off" :disabled="!!busy" class="input mt-1.5 w-full px-3 py-2 text-sm" required>
      </label>
      <label class="text-xs text-ink-2" for="bili-password">B 站密码
        <input id="bili-password" v-model="password" type="password" autocomplete="off" :disabled="!!busy" class="input mt-1.5 w-full px-3 py-2 text-sm" required>
      </label>
      <div class="flex items-center gap-3 sm:col-span-2">
        <button type="submit" class="btn-primary px-4 py-2 text-sm" :disabled="!!busy || !username.trim() || !password">
          {{ busy ? '验证登录中…' : '验证并登录' }}
        </button>
        <span class="text-xs text-muted">密码仅用于本次登录</span>
      </div>
    </form>
    <form v-else class="grid max-w-xl gap-3 sm:grid-cols-[10rem_1fr]" @submit.prevent="submitSms">
      <label class="text-xs text-ink-2" for="bili-country">国家或地区
        <select id="bili-country" v-model="cid" :disabled="!!busy" class="select mt-1.5 w-full px-3 py-2 text-sm">
          <option v-for="country in countries" :key="country.id" :value="country.id">{{ country.name }} +{{ country.code }}</option>
        </select>
      </label>
      <label class="text-xs text-ink-2" for="bili-phone">手机号码
        <input id="bili-phone" v-model="phone" type="tel" inputmode="tel" autocomplete="off" :disabled="!!busy" class="input mt-1.5 w-full px-3 py-2 text-sm" required>
      </label>
      <div class="text-xs text-ink-2 sm:col-span-2">
        <label for="bili-sms-code">短信验证码</label>
        <div class="mt-1.5 flex gap-2">
          <input id="bili-sms-code" v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" :disabled="!!busy" class="input min-w-0 flex-1 px-3 py-2 text-sm" required>
          <button type="button" class="shrink-0 rounded-md border border-line px-3 py-2 text-sm hover:bg-chip disabled:opacity-50" :disabled="!!busy || remaining > 0 || !/^[0-9]{5,20}$/.test(phone.trim())" @click="sendCode">
            {{ busy === 'send' ? '验证发送中…' : remaining > 0 ? `${remaining} 秒后重发` : '获取验证码' }}
          </button>
        </div>
      </div>
      <div class="sm:col-span-2">
        <button type="submit" class="btn-primary px-4 py-2 text-sm" :disabled="!!busy || !captchaKey || !/^[0-9]{4,8}$/.test(code.trim())">
          {{ busy === 'sms' ? '登录中…' : '验证码登录' }}
        </button>
      </div>
    </form>
    <p v-if="error" role="alert" class="mt-3 text-sm text-hot">{{ error }}</p>
    <a v-if="verificationUrl" :href="verificationUrl" target="_blank" rel="noopener noreferrer" class="mt-2 inline-block text-sm text-brand-ink hover:underline">前往 B 站官方完成安全验证 ↗</a>
  </div>
</template>
