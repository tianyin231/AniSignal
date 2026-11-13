/**
 * 站点配置：全服公告 + 文案区域 + 动态分区 + 服务就绪状态（管理端可改；默认值与后端一致）。
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

export interface SiteCommunityItem {
  key: string
  label: string
  en: string
  color: 'blue' | 'yellow' | 'sky' | 'violet' | 'orange' | 'cyan' | 'lime' | 'teal'
  domain: 'game' | 'news'
  platforms: string[]
  active: boolean
  builtin: boolean
  official_bili_mid?: string | null
  aliases?: string[]
}

export interface PublicSiteConfig {
  announcement: SiteAnnouncement
  texts: Record<string, string>
  media_visibility?: MediaVisibility
  communities?: SiteCommunityItem[]
  all_communities?: SiteCommunityItem[]
  ai_configured?: boolean
  smtp_configured?: boolean
}

const MEDIA_DEFAULTS: MediaVisibility = {
  default: { image: 'show', video: 'show' },
  platforms: {},
}

export const TEXT_DEFAULTS: Record<string, string> = {
  footer_note: 'AniSignal · 定制化情报采集',
  contact_email: '',
  source_url: 'https://github.com/tianyin231/AniSignal',
}

export function useSiteConfig() {
  // app.vue 统一取数；内容行只订阅，避免 SSR 每渲染一行就重发配置请求。
  const { data } = useNuxtData<PublicSiteConfig>('site-config')
  const refreshSiteConfig = () => refreshNuxtData('site-config')
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

  /** 未拿到 site-config 前默认视作已配置，避免首屏误闪警告横幅 */
  const aiConfigured = computed(() => data.value ? Boolean(data.value.ai_configured) : true)
  const smtpConfigured = computed(() => data.value ? Boolean(data.value.smtp_configured) : true)

  return { announcement, t, mediaVisibility, mediaMode, aiConfigured, smtpConfigured, refreshSiteConfig }
}
