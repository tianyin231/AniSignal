/**
 * Admin API 客户端：兼容管理员 Token 与已授权的站点管理员账号会话，401 自动清理并回登录页。
 */
export function useAdminApi() {
  const token = useCookie('anisignal_admin_token', { maxAge: 60 * 60 * 24 * 7 })
  const config = useRuntimeConfig()
  const ssrHeaders = import.meta.server ? useRequestHeaders(['cookie']) : undefined
  const { user, fetchMe } = useAuth()

  // SSR 直连 FastAPI；浏览器走同源（Nitro 运行时中间件代理 /api/admin/**）
  const baseURL = import.meta.server ? config.apiSsrBase : config.public.apiBase
  const api = $fetch.create({
    baseURL,
    onRequest({ options }) {
      const headers = new Headers(options.headers)
      if (ssrHeaders?.cookie && !headers.has('cookie')) {
        headers.set('cookie', ssrHeaders.cookie)
      }
      if (token.value) {
        headers.set('X-Admin-Token', token.value)
      }
      options.headers = headers
    },
    onResponseError({ response }) {
      if (response.status === 401) {
        token.value = null
        if (user.value?.is_admin) {
          user.value = { ...user.value, is_admin: false }
        }
        if (import.meta.client && !window.location.pathname.startsWith('/admin/login')) {
          navigateTo('/admin/login')
        }
      }
    },
  })

  async function login(candidate: string, bindCurrentUser = false): Promise<boolean> {
    try {
      await $fetch('/api/admin/collect-targets', {
        baseURL,
        headers: { 'X-Admin-Token': candidate },
      })
    } catch {
      return false
    }
    if (bindCurrentUser) {
      try {
        await $fetch('/api/admin/users/bind-self', {
          method: 'POST',
          baseURL,
          headers: { 'X-Admin-Token': candidate },
        })
        await fetchMe()
        token.value = null
        return true
      } catch {
        // 绑定未成功时仍保留 Token 会话进入后台
      }
    }
    token.value = candidate
    return true
  }

  function logout() {
    token.value = null
  }

  return { api, token, login, logout }
}
