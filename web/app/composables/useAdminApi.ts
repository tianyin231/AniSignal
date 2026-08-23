/**
 * Admin API 客户端：单管理员 Token（cookie 保存），错误统一抛出带状态的 Error。
 */
export function useAdminApi() {
  const token = useCookie('anisignal_admin_token', { maxAge: 60 * 60 * 24 * 7 })
  const config = useRuntimeConfig()

  // SSR 直连 FastAPI；浏览器走同源（Nitro routeRules 代理 /admin/**）
  const baseURL = import.meta.server ? config.apiSsrBase : config.public.apiBase
  const api = $fetch.create({
    baseURL,
    headers: { 'X-Admin-Token': token.value || '' },
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
