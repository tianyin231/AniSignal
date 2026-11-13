import type { AuthUser } from '~/composables/useAuth'

/**
 * /admin 路由守卫：支持管理员 Token 或已绑定的站点管理员账号会话。
 */
export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path === '/admin/login') return
  const token = useCookie('anisignal_admin_token')
  if (token.value) return

  const { user, loaded, fetchMe } = useAuth()
  if (import.meta.server) {
    const cookieHeader = useRequestHeaders(['cookie'])
    if (cookieHeader.cookie) {
      try {
        const config = useRuntimeConfig()
        const res = await $fetch<{ user: AuthUser | null }>('/api/auth/session', {
          baseURL: config.apiSsrBase,
          headers: cookieHeader,
        })
        user.value = res.user
        loaded.value = true
      } catch {
        user.value = null
      }
    }
  } else if (!loaded.value) {
    await fetchMe()
  }

  if (user.value?.is_admin) return
  return navigateTo('/admin/login', { replace: true })
})
