import { platformMeta, type Platform } from '~/utils/data'

export function customKindLabel(kind: string) {
  return ({ uid: 'UP 主监控', keyword: '关键词监控', notices: '官方公告', channel_rss: '官方视频', account_dynamics: '官方动态' } as Record<string, string>)[kind] || '定制监控'
}

export function customPlatformBadge(platform: string, contentType: string) {
  return ({ youtube: 'YT', x: 'X', game_official: '官' } as Record<string, string>)[platform]
    || (contentType === 'dynamic' ? '动' : 'B')
}

/** 监控卡片的来源标识：沿用平台色块（B站粉 / YouTube 红 / X 黑 / 官网绿）。 */
export function customFeedBadge(platform: string): { short: string, badge: string, label: string } {
  const meta = platformMeta[platform as Platform]
  return meta ? { short: meta.short, badge: meta.badge, label: meta.label } : { short: '监', badge: 'bg-brand', label: '定制监控' }
}
