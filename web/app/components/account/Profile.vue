<script setup lang="ts">
import { BadgeCheck, LogOut } from 'lucide-vue-next'
import { apiErrorDetail, apiErrorStatus } from '~/utils/api-errors'
import { AVATARS, DEFAULT_AVATAR, avatarGradient, displayName } from '~/utils/avatars'
import { fmtTime } from '~/utils/admin-types'

// 账号与安全：昵称头像、登录信息、修改密码、退出登录。
const center = useAccountCenter()
const { user, updateProfile, fetchMe, logout } = useAuth()

// ---------- 资料 ----------
const nameDraft = ref('')
const avatarDraft = ref(DEFAULT_AVATAR)
const profileBusy = ref(false)
const profileError = ref('')
const profileMessage = ref('')
watch(user, (value) => {
  nameDraft.value = value?.display_name ?? ''
  avatarDraft.value = value?.avatar ?? DEFAULT_AVATAR
}, { immediate: true })
const previewName = computed(() => displayName({ email: user.value?.email ?? '', display_name: nameDraft.value }))
const profileDirty = computed(() => nameDraft.value.trim() !== (user.value?.display_name ?? '') || avatarDraft.value !== (user.value?.avatar ?? DEFAULT_AVATAR))

async function saveProfile() {
  if (profileBusy.value) return
  profileBusy.value = true
  profileError.value = profileMessage.value = ''
  try {
    await updateProfile(nameDraft.value.trim(), avatarDraft.value)
    await center.fetchOverview()
    profileMessage.value = '资料已保存。'
  } catch (exc) {
    if (apiErrorStatus(exc) === 401) user.value = null
    profileError.value = apiErrorDetail(exc, '保存失败，请重试。')
  } finally {
    profileBusy.value = false
  }
}

// ---------- 密码 ----------
const showPassword = ref(false)
const oldPassword = ref('')
const newPassword = ref('')
const passwordBusy = ref(false)
const passwordError = ref('')
const passwordMessage = ref('')
async function changePassword() {
  if (passwordBusy.value || !oldPassword.value || newPassword.value.length < 8) return
  passwordBusy.value = true
  passwordError.value = passwordMessage.value = ''
  try {
    await $fetch('/api/auth/password/change', { method: 'POST', body: { old_password: oldPassword.value, new_password: newPassword.value } })
    oldPassword.value = newPassword.value = ''
    showPassword.value = false
    passwordMessage.value = '密码已修改，其他设备上的登录已全部退出。'
    await fetchMe()
  } catch (exc) {
    if (apiErrorStatus(exc) === 401) user.value = null
    passwordError.value = apiErrorDetail(exc, '修改失败，请重试。')
  } finally {
    passwordBusy.value = false
  }
}

const loggingOut = ref(false)
async function signOut() {
  loggingOut.value = true
  try {
    await logout()
    await navigateTo('/')
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <div class="grid items-start gap-5 lg:grid-cols-2">
    <section class="panel p-5" aria-labelledby="profile-title">
      <h2 id="profile-title" class="text-base font-bold text-ink">个人资料</h2>
      <form class="mt-4" @submit.prevent="saveProfile">
        <div class="flex items-center gap-4">
          <span class="grid size-16 shrink-0 place-items-center rounded-2xl bg-gradient-to-b text-2xl font-bold text-white shadow-card" :class="avatarGradient(avatarDraft)" aria-hidden="true">
            {{ previewName[0]?.toUpperCase() }}
          </span>
          <label class="min-w-0 flex-1 text-xs font-medium text-ink-2">昵称
            <input v-model="nameDraft" maxlength="24" :placeholder="`留空则显示 ${user?.email.split('@')[0]}`" class="input mt-1.5 h-10 w-full font-normal">
          </label>
        </div>
        <fieldset class="mt-5">
          <legend class="text-xs font-medium text-ink-2">头像颜色</legend>
          <div class="mt-2 flex flex-wrap gap-2">
            <button
              v-for="avatar in AVATARS"
              :key="avatar.id"
              type="button"
              :title="avatar.label"
              :aria-label="avatar.label"
              :aria-pressed="avatarDraft === avatar.id"
              class="size-9 rounded-[10px] bg-gradient-to-b transition-transform hover:scale-105"
              :class="[avatar.gradient, avatarDraft === avatar.id ? 'ring-2 ring-brand ring-offset-2 ring-offset-card' : '']"
              @click="avatarDraft = avatar.id"
            />
          </div>
        </fieldset>
        <div class="mt-5 flex flex-wrap items-center gap-3">
          <button type="submit" class="btn-primary h-9 px-4" :disabled="profileBusy || !profileDirty">{{ profileBusy ? '保存中…' : '保存资料' }}</button>
          <span v-if="profileMessage && !profileDirty" role="status" class="text-xs text-ok">{{ profileMessage }}</span>
        </div>
        <UiNotice v-if="profileError" tone="error" class="mt-3">{{ profileError }}</UiNotice>
      </form>
    </section>

    <div class="space-y-5">
      <section class="panel p-5" aria-labelledby="security-title">
        <h2 id="security-title" class="text-base font-bold text-ink">登录与安全</h2>
        <dl class="mt-3 divide-y divide-line-soft text-sm">
          <div class="flex items-center justify-between gap-3 py-2.5">
            <dt class="text-muted">邮箱</dt>
            <dd class="flex min-w-0 items-center gap-1.5 font-medium text-ink">
              <span class="truncate">{{ user?.email }}</span>
              <BadgeCheck v-if="user?.is_verified" :size="16" class="shrink-0 text-ok" aria-label="已验证" />
            </dd>
          </div>
          <div class="flex items-center justify-between gap-3 py-2.5">
            <dt class="text-muted">注册时间</dt>
            <dd class="mono text-xs text-ink-2">{{ fmtTime(center.overview.value?.created_at ?? user?.created_at ?? null) }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3 py-2.5">
            <dt class="text-muted">最近登录</dt>
            <dd class="mono text-xs text-ink-2">{{ fmtTime(center.overview.value?.last_login_at ?? user?.last_login_at ?? null) }}</dd>
          </div>
          <div class="py-2.5">
            <div class="flex items-center justify-between gap-3">
              <dt class="text-muted">密码</dt>
              <dd>
                <button type="button" class="h-8 rounded-lg px-2.5 text-[13px] font-medium text-brand-ink hover:bg-brand-soft" :aria-expanded="showPassword" @click="showPassword = !showPassword">
                  {{ showPassword ? '收起' : '修改密码' }}
                </button>
              </dd>
            </div>
            <form v-if="showPassword" class="mt-2 space-y-2" @submit.prevent="changePassword">
              <AuthPasswordField id="old-password" v-model="oldPassword" label="原密码" autocomplete="current-password" />
              <AuthPasswordField id="new-password" v-model="newPassword" label="新密码" autocomplete="new-password" hint="至少 8 位；修改后其他设备会退出登录" />
              <UiNotice v-if="passwordError" tone="error">{{ passwordError }}</UiNotice>
              <button type="submit" class="btn-primary h-9 px-4" :disabled="passwordBusy || !oldPassword || newPassword.length < 8">{{ passwordBusy ? '提交中…' : '确认修改' }}</button>
            </form>
          </div>
        </dl>
        <UiNotice v-if="passwordMessage" tone="ok" class="mt-2">{{ passwordMessage }}</UiNotice>
      </section>

      <section class="panel flex items-center justify-between gap-3 p-5">
        <div>
          <h2 class="text-base font-bold text-ink">退出登录</h2>
          <p class="mt-0.5 text-xs text-muted">只退出当前设备。</p>
        </div>
        <button type="button" class="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-line px-3.5 text-sm font-semibold text-hot transition-colors hover:bg-hot/10" :disabled="loggingOut" @click="signOut">
          <LogOut :size="15" /> {{ loggingOut ? '退出中…' : '退出登录' }}
        </button>
      </section>
    </div>
  </div>
</template>
