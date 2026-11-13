export default defineEventHandler((event) => {
  const path = event.path.split('?')[0] || ''
  if (path === '/api/internal' || path.startsWith('/api/internal/')) {
    throw createError({ statusCode: 404, statusMessage: 'Not Found' })
  }
})
