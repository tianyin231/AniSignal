/** $fetch（ofetch）错误：后端 detail 文案与 HTTP 状态码。 */
export function apiErrorDetail(exc: unknown, fallback: string): string {
  const detail = (exc as { data?: { detail?: unknown } } | null)?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

export function apiErrorStatus(exc: unknown): number | undefined {
  const error = exc as { statusCode?: number, status?: number } | null
  return error?.statusCode ?? error?.status
}
