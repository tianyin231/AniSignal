/**
 * 随机壁纸背景：与浅色/深色主题同级的用户偏好（localStorage 持久化）。
 * 开启时经后端 /api/wallpaper（302 到解析后的原图，10 分钟缓存）加载；
 * 加载失败回退默认极光背景，不打断页面。下载走 /api/wallpaper/download，
 * 与展示共用同一解析缓存，保证下载的正是当前看到的那张。
 */

export type WallpaperStatus = 'off' | 'loading' | 'on' | 'failed'

const STORAGE_KEY = 'anisignal-wallpaper'

export function useWallpaper() {
  const status = useState<WallpaperStatus>('wallpaper-status', () => 'off')
  // 实际应用的背景地址（带 cache-bust，每次开启取新图）
  const src = useState<string>('wallpaper-src', () => '')

  function apply() {
    status.value = 'loading'
    src.value = ''
    const url = `/api/wallpaper?t=${Date.now()}`
    const probe = new Image()
    probe.onload = () => {
      if (status.value !== 'loading') return // 已被关闭或重新开启
      src.value = url
      status.value = 'on'
    }
    probe.onerror = () => {
      if (status.value !== 'loading') return
      status.value = 'failed'
    }
    probe.src = url
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
  }

  /** 页面加载时恢复用户上次的选择（由 client 插件调用一次）。 */
  function restore() {
    try {
      if (localStorage.getItem(STORAGE_KEY) === '1') apply()
    } catch { /* 无法读取时视为未开启 */ }
  }

  return { status, src, enable, disable, restore }
}
