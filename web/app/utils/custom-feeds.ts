export function customKindLabel(kind: string) {
  return ({ uid: 'UID 监控', keyword: '关键词监控', notices: '官方公告', channel_rss: '官方视频', account_dynamics: '官方动态' } as Record<string, string>)[kind] || '定制监控'
}

export function customPlatformBadge(platform: string, contentType: string) {
  return ({ youtube: 'YT', x: 'X', game_official: '官' } as Record<string, string>)[platform]
    || (contentType === 'dynamic' ? '动' : 'B')
}
