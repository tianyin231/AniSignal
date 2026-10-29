export function customKindLabel(kind: string) {
  return ({ uid: 'UID 监控', keyword: '关键词监控', notices: '官方公告' } as Record<string, string>)[kind] || '定制监控'
}
