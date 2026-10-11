/**
 * 站点配置：全服公告 + 文案区域（管理端可改；默认值与后端 DEFAULT_TEXTS 对应）。
 */

export interface SiteAnnouncement {
  enabled: boolean
  level: 'info' | 'warning'
  text: string
  link: string
}

export type MediaMode = 'show' | 'link' | 'hide'
export interface MediaModes {
  image: MediaMode
  video: MediaMode
}
export interface MediaVisibility {
  default: MediaModes
  platforms: Record<string, Partial<MediaModes>>
}

export interface PublicSiteConfig {
  announcement: SiteAnnouncement
  texts: Record<string, string>
  media_visibility?: MediaVisibility
}

const MEDIA_DEFAULTS: MediaVisibility = {
  default: { image: 'show', video: 'show' },
  platforms: {},
}

export const TEXT_DEFAULTS: Record<string, string> = {
  footer_note: 'AniSignal · 定制化情报采集',
}

export function useSiteConfig() {
  // app.vue 统一取数；内容行只订阅，避免 SSR 每渲染一行就重发配置请求。
  const { data } = useNuxtData<PublicSiteConfig>('site-config')
  const announcement = computed<SiteAnnouncement>(
    () => data.value?.announcement ?? { enabled: false, level: 'info', text: '', link: '' },
  )
  const t = (key: string) => data.value?.texts?.[key] ?? TEXT_DEFAULTS[key] ?? ''

  const mediaVisibility = computed<MediaVisibility>(() => ({
    default: { ...MEDIA_DEFAULTS.default, ...(data.value?.media_visibility?.default ?? {}) },
    platforms: { ...MEDIA_DEFAULTS.platforms, ...(data.value?.media_visibility?.platforms ?? {}) },
  }))

  /** 解析某平台某类媒体的展示方式：平台覆盖优先，回落全站默认。 */
  const mediaMode = (platform: string, kind: 'image' | 'video'): MediaMode =>
    mediaVisibility.value.platforms[platform]?.[kind] ?? mediaVisibility.value.default[kind] ?? 'show'

  return { announcement, t, mediaVisibility, mediaMode }
}
