import { gzip } from 'node:zlib'
import { promisify } from 'node:util'
import { getRequestHeader } from 'h3'

const gzipHtml = promisify(gzip)

export default defineNitroPlugin((nitroApp) => {
  // compressPublicAssets 只处理静态文件；SSR HTML 也需要按客户端能力压缩。
  nitroApp.hooks.hook('render:response', async (response, { event }) => {
    const headers = response.headers
    if (typeof response.body !== 'string' || !headers?.['content-type']?.includes('text/html')
      || response.body.length < 1024 || headers['content-encoding']) return

    headers.vary = [headers.vary, 'Accept-Encoding'].filter(Boolean).join(', ')
    const acceptsGzip = (getRequestHeader(event, 'accept-encoding') ?? '').split(',').some(value => {
      const [encoding, ...parameters] = value.trim().toLowerCase().split(';')
      return encoding === 'gzip' && parameters.every(parameter => {
        const [key, quality] = parameter.trim().split('=')
        return key !== 'q' || Number(quality) > 0
      })
    })
    if (!acceptsGzip) return

    response.body = await gzipHtml(response.body, { level: 4 })
    headers['content-encoding'] = 'gzip'
    delete headers['content-length']
  })
})
