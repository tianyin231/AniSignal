/**
 * 站点配置：全服公告 + 文案区域（管理端可改；默认值与后端 DEFAULT_TEXTS 对应）。
 */

export interface SiteAnnouncement {
  enabled: boolean
  level: 'info' | 'warning'
  text: string
  link: string
}

export const TEXT_DEFAULTS: Record<string, string> = {
  footer_note: 'AniSignal · 鸣潮事件雷达 · 定制化情报采集',
}

export function useSiteConfig() {
  const { api } = usePublicApi()
  const { data } = useAsyncData('site-config', () =>
    api<{ announcement: SiteAnnouncement; texts: Record<string, string> }>('/api/site-config'),
  )
  const announcement = computed<SiteAnnouncement>(
    () => data.value?.announcement ?? { enabled: false, level: 'info', text: '', link: '' },
  )
  const t = (key: string) => data.value?.texts?.[key] ?? TEXT_DEFAULTS[key] ?? ''
  return { announcement, t }
}
