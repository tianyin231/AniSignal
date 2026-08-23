/**
 * 管理端 toast：所有异步操作的可感知反馈（成功/失败 + 时间戳）。
 */

export interface AdminToast {
  id: number
  type: 'ok' | 'err' | 'info'
  text: string
  at: string
}

let seq = 0

export function useAdminToast() {
  const toasts = useState<AdminToast[]>('admin-toasts', () => [])

  function now() {
    return new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }

  function push(type: AdminToast['type'], text: string, duration = 4000) {
    const id = ++seq
    toasts.value.push({ id, type, text, at: now() })
    if (toasts.value.length > 5) toasts.value.shift()
    setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, duration)
  }

  return {
    toasts,
    ok: (text: string) => push('ok', text),
    err: (text: string) => push('err', text, 6000),
    info: (text: string) => push('info', text),
  }
}
