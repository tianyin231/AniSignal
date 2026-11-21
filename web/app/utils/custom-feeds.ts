import { platformMeta, type Platform } from './data.ts'

export const XIAOHEIHE_MONITOR = {
  kind: 'hot_posts', platform: 'xiaoheihe', target: 'hot', name: '小黑盒热帖',
} as const
export const XIAOHEIHE_ACCOUNT_MONITOR = {
  kind: 'xhh_account', platform: 'xiaoheihe', name: '小黑盒账号',
} as const
export const XIAOHEIHE_KEYWORD_MONITOR = {
  kind: 'xhh_keyword', platform: 'xiaoheihe', name: '小黑盒关键词',
} as const

export function customKindLabel(kind: string) {
  return ({
    uid: 'UP 主监控',
    keyword: '关键词监控',
    feed: 'RSS 订阅',
    channel_rss: 'YouTube 频道',
    notices: '官方公告',
    account_dynamics: '官方动态',
    [XIAOHEIHE_MONITOR.kind]: XIAOHEIHE_MONITOR.name,
    [XIAOHEIHE_ACCOUNT_MONITOR.kind]: XIAOHEIHE_ACCOUNT_MONITOR.name,
    [XIAOHEIHE_KEYWORD_MONITOR.kind]: XIAOHEIHE_KEYWORD_MONITOR.name,
  } as Record<string, string>)[kind] || '定制监控'
}

export function customPlatformBadge(platform: string, contentType: string) {
  return ({ youtube: 'YT', rss: 'RSS', x: 'X', game_official: '官', xiaoheihe: '盒' } as Record<string, string>)[platform]
    || (contentType === 'dynamic' ? '动' : 'B')
}

/** 监控卡片的来源标识：沿用平台色块（B站粉 / YouTube 红 / X 黑 / 官网绿）。 */
export function customFeedBadge(platform: string): { short: string, badge: string, label: string } {
  const meta = platformMeta[platform as Platform]
  return meta ? { short: meta.short, badge: meta.badge, label: meta.label } : { short: '监', badge: 'bg-brand', label: '定制监控' }
}
