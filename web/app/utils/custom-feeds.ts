import { CUSTOM_KIND_LABELS, PLATFORM_META, type PlatformKey } from './lexicon.ts'

export const XIAOHEIHE_MONITOR = {
  kind: 'hot_posts', platform: 'xiaoheihe', target: 'hot', name: '小黑盒热帖',
} as const
export const XIAOHEIHE_ACCOUNT_MONITOR = {
  kind: 'xhh_account', platform: 'xiaoheihe', name: '小黑盒账号',
} as const
export const XIAOHEIHE_KEYWORD_MONITOR = {
  kind: 'xhh_keyword', platform: 'xiaoheihe', name: '小黑盒关键词',
} as const
export const STEAM_MONITOR = {
  kind: 'steam_app', platform: 'steam', name: 'Steam 游戏',
} as const
export const WEB_PAGE_MONITOR = {
  kind: 'page_watch', platform: 'web_page', name: '单页变更',
} as const
export const WEB_LIST_MONITOR = {
  kind: 'list_watch', platform: 'web_page', name: '列表更新',
} as const
export const GH_RELEASES_MONITOR = {
  kind: 'gh_releases', platform: 'github', name: 'GitHub 版本',
} as const

export function customKindLabel(kind: string) {
  return CUSTOM_KIND_LABELS[kind] || '定制监控'
}

export function customPlatformBadge(platform: string, contentType: string) {
  return PLATFORM_META[platform as PlatformKey]?.short
    || (contentType === 'dynamic' ? '动' : 'B')
}

/** 监控卡片的来源标识：沿用平台色块（B 站粉 / YouTube 红 / X 黑 / 官网绿）。 */
export function customFeedBadge(platform: string): { short: string, badge: string, label: string } {
  const meta = PLATFORM_META[platform as PlatformKey]
  return meta ? { short: meta.short, badge: meta.badge, label: meta.label } : { short: '监', badge: 'bg-brand', label: '定制监控' }
}
