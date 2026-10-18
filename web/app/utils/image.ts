// 展示图直接使用原 CDN 的缩放能力；原文和下载链接仍保留原图。
const BILI_IMAGE = /^https?:\/\/i\d\.hdslb\.com\/bfs\//i

export function imageUrl(src: string | undefined, width: number): string | undefined {
  if (!src || !BILI_IMAGE.test(src)) return src
  const url = new URL(src)
  url.pathname = `${url.pathname.split('@')[0]}@${width}w.webp`
  return url.href
}

export function imageSrcset(src: string | undefined, widths: number[]): string | undefined {
  if (!src || !BILI_IMAGE.test(src)) return undefined
  return widths.map(width => `${imageUrl(src, width)} ${width}w`).join(', ')
}
