/**
 * 账号状态：客户端拉取 /api/auth/me（cookie 同源自动携带）。
 */
import { safeRedirect } from '~/utils/safe-redirect'

export interface AuthUser {
  id: number
  email: string
  display_name?: string | null
  avatar?: string | null
  is_verified: boolean
  created_at: string | null
  last_login_at: string | null
}

// 顶栏、底栏和页面会在同一次挂载里各自调用 fetchMe；客户端共用一个进行中的请求。
let pendingMe: Promise<void> | null = null

export function useAuth() {
  const user = useState<AuthUser | null>('auth-user', () => null)
  const loaded = useState<boolean>('auth-loaded', () => false)
  // 主动退出期间为 true：需要登录的页面据此区分"退出登录"和"会话过期"，前者不跳登录页
  const signingOut = useState<boolean>('auth-signing-out', () => false)

  function fetchMe(): Promise<void> {
    if (import.meta.client && pendingMe) return pendingMe
    const run = (async () => {
      try {
        user.value = await $fetch<AuthUser>('/api/auth/me')
      } catch {
        user.value = null
      } finally {
        loaded.value = true
      }
    })()
    if (import.meta.client) {
      pendingMe = run
      run.finally(() => { pendingMe = null })
    }
    return run
  }

  async function login(email: string, password: string) {
    await $fetch('/api/auth/login', { method: 'POST', body: { email, password } })
    await fetchMe()
  }

  async function updateProfile(display_name: string, avatar: string) {
    await $fetch('/api/auth/profile', { method: 'PUT', body: { display_name, avatar } })
    await fetchMe()
  }

  async function logout() {
    signingOut.value = true
    try {
      await $fetch('/api/auth/logout', { method: 'POST' })
      user.value = null
    } finally {
      signingOut.value = false
    }
  }

  onMounted(() => {
    if (!loaded.value) fetchMe()
  })

  return { user, loaded, signingOut, fetchMe, login, logout, updateProfile }
}

/** 认证页之间跳转时保留 ?redirect=，完成后回到来源页。 */
export function useAuthRedirect() {
  const route = useRoute()
  const origin = useRequestURL().origin
  const redirect = computed(() => (typeof route.query.redirect === 'string' ? route.query.redirect : undefined))
  const target = () => safeRedirect(route.query.redirect, import.meta.client ? window.location.origin : origin)
  const withRedirect = (path: string) => (redirect.value ? { path, query: { redirect: redirect.value } } : { path })
  return { redirect, target, withRedirect }
}

/** 认证页之间传递的一次性信息：已填写的邮箱、"密码已重置"等提示（不放进 URL）。 */
export function useAuthFlash() {
  return useState<{ email?: string, message?: string } | null>('auth-flash', () => null)
}
