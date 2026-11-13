/**
 * Admin API 客户端：单管理员 Token（cookie 保存），401 自动清理并跳转登录页。
 */
export function useAdminApi() {
  const token = useCookie('anisignal_admin_token', { maxAge: 60 * 60 * 24 * 7 })
  const config = useRuntimeConfig()

  // SSR 直连 FastAPI；浏览器走同源（Nitro routeRules 代理 /api/admin/**）
  const baseURL = import.meta.server ? config.apiSsrBase : config.public.apiBase
  const api = $fetch.create({
    baseURL,
    onRequest({ options }) {
      const headers = new Headers(options.headers)
      headers.set('X-Admin-Token', token.value || '')
      options.headers = headers
    },
    onResponseError({ response }) {
      if (response.status === 401) {
        token.value = null
        if (import.meta.client && !window.location.pathname.startsWith('/admin/login')) {
          navigateTo('/admin/login')
        }
      }
    },
  })

  async function login(candidate: string): Promise<boolean> {
    try {
      await $fetch('/api/admin/collect-targets', {
        baseURL,
        headers: { 'X-Admin-Token': candidate },
      })
    } catch {
      return false
    }
    token.value = candidate
    return true
  }

  function logout() {
    token.value = null
  }

  return { api, token, login, logout }
}
