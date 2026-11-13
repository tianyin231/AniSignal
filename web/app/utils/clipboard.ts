/**
 * 兼容局域网纯 HTTP（非 SecureContext）的复制与随机 ID 工具。
 */
export async function copyText(value: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard && typeof window !== 'undefined' && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // 继续走 textarea 降级复制
    }
  }
  if (typeof document === 'undefined') return false
  const el = document.createElement('textarea')
  el.value = value
  el.setAttribute('readonly', 'true')
  el.style.position = 'fixed'
  el.style.opacity = '0'
  document.body.appendChild(el)
  el.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } finally {
    document.body.removeChild(el)
  }
  return ok
}

export function safeRandomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
