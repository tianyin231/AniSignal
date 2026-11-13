import { createError, defineEventHandler, getRequestHeader, proxyRequest } from 'h3'

const ROOT_API_PATHS = new Set(['/rss.xml', '/sitemap.xml', '/robots.txt'])

export default defineEventHandler((event) => {
  const rawPath = event.path || '/'
  const qIndex = rawPath.indexOf('?')
  const pathname = qIndex >= 0 ? rawPath.slice(0, qIndex) : rawPath
  const search = qIndex >= 0 ? rawPath.slice(qIndex) : ''

  if (pathname === '/api/internal' || pathname.startsWith('/api/internal/')) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }

  let upstreamPath: string | null = null
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    upstreamPath = rawPath
  } else if (ROOT_API_PATHS.has(pathname)) {
    upstreamPath = `/api${pathname}${search}`
  }
  if (!upstreamPath) return

  const base = String(useRuntimeConfig(event).apiSsrBase || 'http://127.0.0.1:8000').replace(/\/+$/, '')
  const host = getRequestHeader(event, 'x-forwarded-host') || getRequestHeader(event, 'host')
  return proxyRequest(event, `${base}${upstreamPath}`, {
    headers: host ? { 'x-forwarded-host': host } : undefined,
  })
})
