/**
 * 登录成功后的站内跳转目标（SEC-040）。
 *
 * 旧的字符串前缀判据（`target.startsWith('/') && !target.startsWith('//')`）挡不住
 * `/\evil.com`：WHATWG URL 解析器在 special scheme 下会把路径里的 `\` 规范化成 `/`，
 * 于是 `/\evil.com` 实际解析成 `https://evil.com/`——它既 startsWith('/') 又不
 * startsWith('//')，旧判据全放行，登录成功的瞬间用户就被送到仿冒登录页。
 * 同类可绕写法 `/\/evil.com`、`/\\evil.com`、`https://evil.com` 一样。
 *
 * 唯一可靠的判据是「解析后的 origin 必须还是本站」，所以交给 `URL` 而不是自己数字符：
 * 反斜杠、多余斜杠、控制字符、绝对地址在这一个判据下全部收敛。`startsWith('//')`
 * 那层保留只是廉价的前置闸，不依赖它兜底。
 */
export function safeRedirect(target: unknown, origin: string, fallback = '/account'): string {
  const raw = typeof target === 'string' ? target : ''
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return fallback
  if (!origin) return fallback
  try {
    const url = new URL(raw, origin)
    if (url.origin !== origin) return fallback
    // 只回 pathname/search/hash：protocol 与 host 已被 origin 判据锁死，不带回去
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return fallback
  }
}
