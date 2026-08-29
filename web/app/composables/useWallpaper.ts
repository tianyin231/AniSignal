/**
 * 随机壁纸背景：与浅色/深色主题同级的用户偏好（localStorage 持久化）。
 * 流式加载：/api/wallpaper 返回原图地址（下载用）+ 服务端压缩图 + 32px 模糊预览，
 * 预览先出（~1KB），压缩图（几百 KB）随后流入，失败回退默认极光背景。
 */

export type WallpaperStatus = 'off' | 'loading' | 'on' | 'failed'

const STORAGE_KEY = 'anisignal-wallpaper'

export function useWallpaper() {
  const status = useState<WallpaperStatus>('wallpaper-status', () => 'off')
  // 展示用压缩图地址（服务端转码缓存，带长 Cache-Control）
  const src = useState<string>('wallpaper-src', () => '')
  // 模糊预览地址（先于高清图显示）
  const preview = useState<string>('wallpaper-preview', () => '')
  // 原图地址（仅用于下载链接）
  const origin = useState<string>('wallpaper-origin', () => '')

  function apply() {
    status.value = 'loading'
    src.value = ''
    preview.value = ''
    origin.value = ''
    $fetch<{ url?: string; image?: string; preview?: string }>('/api/wallpaper')
      .then((res) => {
        if (status.value !== 'loading' || !res?.url || !res.image) {
          if (status.value === 'loading') status.value = 'failed'
          return
        }
        origin.value = res.url
        src.value = res.image
        preview.value = res.preview ?? ''
        // 预览层立即渲染（backdrop 在 loading 态即显示），压缩图加载完成再切 on
        const probe = new Image()
        probe.onload = () => {
          if (status.value !== 'loading') return
          status.value = 'on'
        }
        probe.onerror = () => {
          if (status.value !== 'loading') return
          status.value = 'failed'
        }
        probe.src = res.image
      })
      .catch(() => {
        if (status.value === 'loading') status.value = 'failed'
      })
  }

  function persist(value: string) {
    try {
      localStorage.setItem(STORAGE_KEY, value)
    } catch { /* 隐私模式等场景下不可写，忽略 */ }
  }

  function enable() {
    if (status.value === 'loading') return
    persist('1')
    apply()
  }

  function disable() {
    persist('0')
    status.value = 'off'
    src.value = ''
    preview.value = ''
    origin.value = ''
  }

  /** 页面加载时恢复用户上次的选择（由 client 插件调用一次）。 */
  function restore() {
    try {
      if (localStorage.getItem(STORAGE_KEY) === '1') apply()
    } catch { /* 无法读取时视为未开启 */ }
  }

  return { status, src, preview, origin, enable, disable, restore }
}
