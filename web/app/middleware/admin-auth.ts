/**
 * /admin 路由守卫：无 Token 一律回登录页（login 页自身不挂此中间件）。
 */
export default defineNuxtRouteMiddleware((to) => {
  if (to.path === '/admin/login') return
  const token = useCookie('anisignal_admin_token')
  if (!token.value) return navigateTo('/admin/login', { replace: true })
})
