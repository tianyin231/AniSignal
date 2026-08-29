/**
 * 随机壁纸背景：与浅色/深色主题同级的用户偏好（localStorage 持久化）。
 * 开启时向后端 /api/wallpaper 要一个新解析的原图地址（每次页面加载基本不同），
 * 直接热链显示；加载失败回退默认极光背景。下载走 /api/wallpaper/download?url=<当前地址>，
 * 服务端只转发自己签发过的地址，保证下载的就是当前展示的那张。
 */

export type WallpaperStatus = 'off' | 'loading' | 'on' | 'failed'

const STORAGE_KEY = 'anisignal-wallpaper'

export function useWallpaper() {
  const status = useState<WallpaperStatus>('wallpaper-status', () => 'off')
  // 当前展示的原图地址（热链），同时用于下载
  const src = useState<string>('wallpaper-src', () => '')

  function apply() {
    status.value = 'loading'
    src.value = ''
    $fetch<{ url?: string }>('/api/wallpaper')
      .then((res) => {
        const url = res?.url
        if (!url || status.value !== 'loading') return
        const probe = new Image()
        probe.onload = () => {
          if (status.value !== 'loading') return
          src.value = url
          status.value = 'on'
        }
        probe.onerror = () => {
          if (status.value !== 'loading') return
          status.value = 'failed'
        }
        probe.referrerPolicy = 'no-referrer'
        probe.src = url
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
  }

  /** 页面加载时恢复用户上次的选择（由 client 插件调用一次）。 */
  function restore() {
    try {
      if (localStorage.getItem(STORAGE_KEY) === '1') apply()
    } catch { /* 无法读取时视为未开启 */ }
  }

  return { status, src, enable, disable, restore }
}
