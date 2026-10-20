/**
 * 登录成功后的站内跳转目标。
 *
 * 旧的字符串前缀判据（`target.startsWith('/') && !target.startsWith('//')`）挡不住
 * `/\evil.com`：WHATWG URL 解析器在 special scheme 下会把路径里的 `\` 规范化成 `/`，
 * 于是 `/\evil.com` 实际解析成 `https://evil.com/`——它既 startsWith('/') 又不
 * startsWith('//')，旧判据全放行，登录成功的瞬间用户就被送到仿冒登录页。
 *
 * 唯一可靠的判据是「解析后的 origin 必须还是本站」，所以交给 `URL` 而不是自己数字符。
 *
 * 注意还要判归一化**之后**的 pathname：`/..//evil.com`、`/.//evil.com` 解析出的
 * origin 仍是本站，但 pathname 会被点段归一化成 `//evil.com`（协议相对地址），直接
 * 拼回去就是站外跳转。所以对结果再判一次单斜杠，并用 URL 复核一次 origin。
 */
export function safeRedirect(target: unknown, origin: string, fallback = '/account'): string {
	const raw = typeof target === 'string' ? target : ''
	if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return fallback
	if (!origin) return fallback
	try {
		const url = new URL(raw, origin)
		if (url.origin !== origin) return fallback
		// 只回 pathname/search/hash：protocol 与 host 已被 origin 判据锁死
		const path = `${url.pathname}${url.search}${url.hash}`
		// 归一化后的 pathname 仍可能是 //host，拼回去就是协议相对地址
		if (!path.startsWith('/') || path.startsWith('//') || path.startsWith('/\\')) return fallback
		// 最后复核一次：确保这个字符串本身解析后仍在本站（防御纵深）
		if (new URL(path, origin).origin !== origin) return fallback
		return path
	} catch {
		return fallback
	}
}
