// 展示图直接使用原 CDN 的缩放能力；原文和下载链接仍保留原图。
// 封面多数来自第三方 CDN，列表缩略图只有 112–160px 宽，拿原图会白白多下几百 KB。
const BILI_IMAGE = /^https?:\/\/i\d\.hdslb\.com\/bfs\//i
const YOUTUBE_IMAGE = /^https:\/\/i\.ytimg\.com\/vi(?:_webp)?\/([\w-]{6,})\/[\w]+\.(?:jpg|webp)$/i
const TWITTER_IMAGE = /^https:\/\/pbs\.twimg\.com\/(?:media|amplify_video_thumb|ext_tw_video_thumb|tweet_video_thumb)\//i
// 阿里云 OSS 图片处理（x-oss-process）已在这些域名上实测可用
const OSS_HOSTS = new Set(['fastcdn.mihoyo.com', 'upload-bbs.miyoushe.com', 'web.hycdn.cn'])
// X 图床的固定档位：name=360x360 等为"长边不超过"，small/medium/large 为宽度上限
const TWITTER_SIZES: [number, string][] = [[360, '360x360'], [680, 'small'], [1200, 'medium'], [2048, 'large']]

function parse(src: string): URL | null {
  try { return new URL(src) } catch { return null }
}

/** 返回目标宽度的展示图地址，以及该地址实际的宽度档位（用于 srcset 描述符）。 */
function resize(src: string, width: number): { url: string, width: number } | null {
  const url = parse(src)
  if (!url) return null
  if (BILI_IMAGE.test(src)) {
    // B 站封面常是 http:// 地址，统一升级为 https，避免混合内容
    url.protocol = 'https:'
    url.pathname = `${url.pathname.split('@')[0]}@${width}w.webp`
    return { url: url.href, width }
  }
  const youtube = src.match(YOUTUBE_IMAGE)
  if (youtube) {
    // mqdefault 固定 320×180 且不带黑边；更大尺寸保留原图（maxresdefault 不一定存在）
    return width <= 320
      ? { url: `https://i.ytimg.com/vi/${youtube[1]}/mqdefault.jpg`, width: 320 }
      : { url: src, width: 1280 }
  }
  if (TWITTER_IMAGE.test(src)) {
    const [size, name] = TWITTER_SIZES.find(([max]) => width <= max) ?? TWITTER_SIZES.at(-1)!
    url.searchParams.set('name', name)
    if (!url.searchParams.has('format')) url.searchParams.set('format', 'jpg')
    return { url: url.href, width: size }
  }
  if (OSS_HOSTS.has(url.hostname) && !url.searchParams.has('x-oss-process')) {
    url.searchParams.set('x-oss-process', `image/resize,w_${width}/format,webp`)
    return { url: url.href, width }
  }
  return null
}

export function imageUrl(src: string | undefined, width: number): string | undefined {
  if (!src) return src
  return resize(src, width)?.url ?? src
}

export function imageSrcset(src: string | undefined, widths: number[]): string | undefined {
  if (!src) return undefined
  const seen = new Set<string>()
  const parts: string[] = []
  for (const width of widths) {
    const candidate = resize(src, width)
    if (!candidate) return undefined
    if (seen.has(candidate.url)) continue
    seen.add(candidate.url)
    parts.push(`${candidate.url} ${candidate.width}w`)
  }
  return parts.length > 1 ? parts.join(', ') : undefined
}
