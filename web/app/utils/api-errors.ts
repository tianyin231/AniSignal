/** $fetch（ofetch）错误：后端 detail 文案与 HTTP 状态码。 */
export function apiErrorDetail(exc: unknown, fallback: string): string {
  const detail = (exc as { data?: { detail?: unknown } } | null)?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') {
    return detail[0].msg.replace(/^Value error,\s*/i, '')
  }
  return fallback
}

export function apiErrorStatus(exc: unknown): number | undefined {
  const error = exc as { statusCode?: number, status?: number } | null
  return error?.statusCode ?? error?.status
}
