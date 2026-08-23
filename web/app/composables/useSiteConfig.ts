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
  footer_note: 'AniSignal · 鸣潮事件雷达 · 不承诺全网覆盖',
  home_intro: '多平台官方内容自动归并为带证据链的事件；全站免费浏览，登录用于订阅与通知。',
  official_note: '已接入官方来源的原始内容流；跨平台重复公告的合并视图将在事件引擎上线后提供。',
  events_note: '事件由引擎自动聚合、人工复核修正；每条结论可回溯原始证据。',
  sources_note: '本页公开展示已接入来源、最近成功采集时间与不可用状态。全站任何「未发现」均指「在已接入来源中未发现」，不宣传全网覆盖。',
  watch_intro: '邮件通知、早报与监控位正在开发中。上线前，本站全部信息免费开放，无需注册。',
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
