/**
 * 账号状态：客户端拉取 /api/auth/me（cookie 同源自动携带）。
 */

export interface AuthUser {
  id: number
  email: string
  display_name?: string | null
  avatar?: string | null
  is_verified: boolean
  created_at: string | null
  last_login_at: string | null
}

export function useAuth() {
  const user = useState<AuthUser | null>('auth-user', () => null)
  const loaded = useState<boolean>('auth-loaded', () => false)

  async function fetchMe() {
    try {
      user.value = await $fetch<AuthUser>('/api/auth/me')
    } catch {
      user.value = null
    } finally {
      loaded.value = true
    }
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
    await $fetch('/api/auth/logout', { method: 'POST' })
    user.value = null
  }

  onMounted(() => {
    if (!loaded.value) fetchMe()
  })

  return { user, loaded, fetchMe, login, logout, updateProfile }
}
