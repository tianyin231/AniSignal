/**
 * Admin API 客户端：单管理员 Token（cookie 保存），错误统一抛出带状态的 Error。
 */
export function useAdminApi() {
  const token = useCookie('anisignal_admin_token', { maxAge: 60 * 60 * 24 * 7 })
  const config = useRuntimeConfig()

  const api = $fetch.create({
    baseURL: config.public.apiBase,
    headers: { 'X-Admin-Token': token.value || '' },
  })

  async function login(candidate: string): Promise<boolean> {
    try {
      await $fetch(`${config.public.apiBase}/admin/collect-targets`, {
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
