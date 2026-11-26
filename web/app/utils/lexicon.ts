/**
 * AniSignal 全站术语与展示标签的单一真相源（Single Source of Truth）。
 * 遵循 docs/ui-copy-guidelines.md：克制、准确、无歧义的书面产品语；中英数字间保留半角空格。
 */

export type PlatformKey =
  | 'bilibili'
  | 'weibo'
  | 'x'
  | 'youtube'
  | 'web'
  | 'web_page'
  | 'github'
  | 'gitcode'
  | 'kuro'
  | 'miyoushe'
  | 'kuro_cn'
  | 'kuro_global'
  | 'rss'
  | 'game_official'
  | 'xiaoheihe'
  | 'steam'

export interface PlatformInfo {
  label: string
  short: string
  badge: string
  region: string
}

export const PLATFORM_META: Record<PlatformKey, PlatformInfo> = {
  bilibili: { label: 'B 站', short: 'B', badge: 'bg-pink-500', region: '国内' },
  weibo: { label: '微博', short: '博', badge: 'bg-red-600', region: '国内' },
  x: { label: 'X', short: 'X', badge: 'bg-zinc-900 dark:bg-zinc-700', region: '海外' },
  rss: { label: 'RSS 订阅', short: 'RSS', badge: 'bg-teal-700', region: '通用' },
  web_page: { label: '网页监测', short: '页', badge: 'bg-cyan-600', region: '通用' },
  github: { label: 'GitHub', short: 'GH', badge: 'bg-zinc-900 dark:bg-zinc-700', region: '海外' },
  gitcode: { label: 'GitCode', short: 'GC', badge: 'bg-orange-600', region: '国内' },
  youtube: { label: 'YouTube', short: 'YT', badge: 'bg-red-500', region: '海外' },
  game_official: { label: '游戏官网', short: '官', badge: 'bg-emerald-600', region: '官方' },
  web: { label: '官网', short: '网', badge: 'bg-sky-600', region: '通用' },
  kuro: { label: '库街区', short: '库', badge: 'bg-teal-600', region: '国内' },
  miyoushe: { label: '米游社', short: '米', badge: 'bg-sky-500', region: '国内' },
  xiaoheihe: { label: '小黑盒', short: '盒', badge: 'bg-zinc-800 dark:bg-zinc-700', region: '国内' },
  kuro_cn: { label: '库街区·国服', short: '库', badge: 'bg-sky-600', region: '国服' },
  kuro_global: { label: '库街区·国际服', short: '际', badge: 'bg-cyan-600', region: '国际服' },
  steam: { label: 'Steam', short: 'S', badge: 'bg-slate-800 dark:bg-slate-600', region: '国际服' },
}

export function platformInfo(platform: string): PlatformInfo {
  return PLATFORM_META[platform as PlatformKey] ?? {
    label: platform,
    short: platform.slice(0, 1).toUpperCase() || '?',
    badge: 'bg-slate-500',
    region: '通用',
  }
}

export function platformLabel(platform: string): string {
  return PLATFORM_META[platform as PlatformKey]?.label ?? platform
}

export function platformRegion(platform: string): string {
  return PLATFORM_META[platform as PlatformKey]?.region ?? '通用'
}

/** 管理端采集目标平台有序映射 */
const ADMIN_PLATFORM_ORDER: PlatformKey[] = [
  'game_official',
  'rss',
  'web_page',
  'github',
  'gitcode',
  'bilibili',
  'miyoushe',
  'xiaoheihe',
  'kuro_cn',
  'kuro_global',
  'steam',
  'youtube',
  'x',
]

export const ADMIN_PLATFORM_LABELS: Record<string, string> = Object.fromEntries(
  ADMIN_PLATFORM_ORDER.map(key => [key, PLATFORM_META[key].label]),
)

export const TARGET_TYPE_LABELS: Record<string, string> = {
  feed: 'RSS 订阅',
  page_watch: '单页变更',
  list_watch: '列表更新',
  releases: '版本发布',
  issues: '议题动态',
  discussions: '社区讨论',
  trending: '趋势榜单',
  keyword_search: '关键词搜索',
  account_videos: '账号视频',
  account_dynamics: '账号动态',
  notices: '官方公告',
  channel_rss: '频道 RSS',
}

export const CONTENT_TYPE_LABELS: Record<string, string> = {
  video: '视频',
  dynamic: '动态',
  post: '帖子',
  notice: '公告',
  rss_item: 'RSS 订阅',
  web_page: '网页监测',
  release: '版本发布',
  repo: '开源项目',
}

export const TIER_META = {
  core: {
    label: '全部收录',
    desc: '全量入库，不经标题必含词与热度过滤',
  },
  general: {
    label: '条件筛选',
    desc: '按标题必含词与热度门槛筛选入库',
  },
  none: {
    label: '无匹配',
    desc: '',
  },
} as const

export type TierKey = keyof typeof TIER_META

export function tierLabel(tier: string): string {
  return TIER_META[tier as TierKey]?.label ?? tier
}

export type MonitorGroupKey = 'web' | 'code' | 'media' | 'game'

export interface MonitorGroup {
  key: MonitorGroupKey
  label: string
}

export const MONITOR_GROUPS: readonly MonitorGroup[] = [
  { key: 'web', label: '网页与订阅' },
  { key: 'code', label: '开源项目' },
  { key: 'media', label: '视频与社区' },
  { key: 'game', label: '游戏平台' },
] as const

export interface MonitorKindMeta {
  key: string
  platform: PlatformKey
  group: MonitorGroupKey
  label: string
  /** 仅在创建选择节点展示，不超过 14 字 */
  desc: string
  fieldLabel: string
  placeholder: string
}

export const MONITOR_KINDS: readonly MonitorKindMeta[] = [
  {
    key: 'feed',
    platform: 'rss',
    group: 'web',
    label: 'RSS 订阅',
    desc: '订阅 RSS 或 Atom 源',
    fieldLabel: '订阅地址',
    placeholder: 'https://example.com/feed.xml',
  },
  {
    key: 'page_watch',
    platform: 'web_page',
    group: 'web',
    label: '单页变更',
    desc: '监测固定页面正文或表格变动',
    fieldLabel: '目标网页地址',
    placeholder: 'https://example.com/changelog',
  },
  {
    key: 'list_watch',
    platform: 'web_page',
    group: 'web',
    label: '列表更新',
    desc: '监测目录或公告页新增文章',
    fieldLabel: '列表页地址',
    placeholder: 'https://example.com/news',
  },
  {
    key: 'gh_releases',
    platform: 'github',
    group: 'code',
    label: 'GitHub 版本',
    desc: '追踪仓库正式版与预发布版本',
    fieldLabel: 'GitHub 仓库',
    placeholder: 'owner/repo 或 https://github.com/owner/repo',
  },
  {
    key: 'gh_issues',
    platform: 'github',
    group: 'code',
    label: 'GitHub 议题',
    desc: '追踪仓库议题，可按标签筛选',
    fieldLabel: 'GitHub 仓库',
    placeholder: 'owner/repo 或 https://github.com/owner/repo',
  },
  {
    key: 'gh_discussions',
    platform: 'github',
    group: 'code',
    label: 'GitHub 讨论',
    desc: '追踪仓库公开社区讨论动态',
    fieldLabel: 'GitHub 仓库',
    placeholder: 'owner/repo 或 https://github.com/owner/repo',
  },
  {
    key: 'gc_releases',
    platform: 'gitcode',
    group: 'code',
    label: 'GitCode 版本',
    desc: '追踪仓库正式版与预发布版本',
    fieldLabel: 'GitCode 仓库',
    placeholder: 'owner/repo 或 https://gitcode.com/owner/repo',
  },
  {
    key: 'gc_issues',
    platform: 'gitcode',
    group: 'code',
    label: 'GitCode 议题',
    desc: '追踪仓库议题，可按标签筛选',
    fieldLabel: 'GitCode 仓库',
    placeholder: 'owner/repo 或 https://gitcode.com/owner/repo',
  },
  {
    key: 'keyword',
    platform: 'bilibili',
    group: 'media',
    label: 'B 站关键词',
    desc: '按关键词追踪 B 站新投稿',
    fieldLabel: '搜索关键词',
    placeholder: '例如：独立游戏 发售、机械键盘 评测',
  },
  {
    key: 'uid',
    platform: 'bilibili',
    group: 'media',
    label: 'B 站 UP 主',
    desc: '追踪指定 B 站账号视频与动态',
    fieldLabel: 'UP 主 UID',
    placeholder: '纯数字，空间主页地址里的数字编号',
  },
  {
    key: 'channel_rss',
    platform: 'youtube',
    group: 'media',
    label: 'YouTube 频道',
    desc: '追踪 YouTube 频道更新',
    fieldLabel: '频道链接、@handle 或频道 ID',
    placeholder: 'https://www.youtube.com/@xxx、@xxx 或 UC...',
  },
  {
    key: 'xhh_account',
    platform: 'xiaoheihe',
    group: 'media',
    label: '小黑盒账号',
    desc: '追踪指定小黑盒用户发帖',
    fieldLabel: '小黑盒 UID 或主页链接',
    placeholder: '例如 12345678 或 https://xiaoheihe.cn/app/user/profile/12345678',
  },
  {
    key: 'xhh_keyword',
    platform: 'xiaoheihe',
    group: 'media',
    label: '小黑盒关键词',
    desc: '按关键词检索小黑盒帖子',
    fieldLabel: '小黑盒搜索关键词',
    placeholder: '例如：兑换码、联名键盘、鸣潮',
  },
  {
    key: 'hot_posts',
    platform: 'xiaoheihe',
    group: 'media',
    label: '小黑盒热帖',
    desc: '关注小黑盒热门帖子榜单',
    fieldLabel: '',
    placeholder: '',
  },
  {
    key: 'steam_app',
    platform: 'steam',
    group: 'game',
    label: 'Steam 游戏',
    desc: '追踪 Steam 游戏更新公告',
    fieldLabel: 'Steam AppID 或商店链接',
    placeholder: '例如 3513350 或 https://store.steampowered.com/app/3513350/',
  },
] as const

export const CUSTOM_KIND_LABELS: Record<string, string> = {
  ...Object.fromEntries(MONITOR_KINDS.map(k => [k.key, k.label])),
  uid: 'B 站 UP 主',
  keyword: 'B 站关键词',
  notices: '官方公告',
  account_dynamics: '官方动态',
}

/** 将网页解析引擎内部标识映射为用户可读语义 */
export function webExtractorLabel(extractor: string): string {
  const key = (extractor || '').toLowerCase()
  if (key.includes('selector') || key.includes('xpath') || key.includes('css')) return '指定区域'
  if (key.includes('table')) return '表格提取'
  if (key.includes('list') || key.includes('link')) return '列表解析'
  if (key.includes('trafilatura') || key.includes('readability') || key.includes('html2text') || key.includes('dom')) return '正文提取'
  return extractor || '自动提取'
}

export interface AdminNavItem {
  to: string
  label: string
}

export interface AdminNavGroup {
  key: string
  label: string
  items: readonly AdminNavItem[]
}

export const ADMIN_NAV_GROUPS: readonly AdminNavGroup[] = [
  {
    key: 'overview',
    label: '概览',
    items: [
      { to: '/admin', label: '总览' },
    ],
  },
  {
    key: 'sources',
    label: '信源与采集',
    items: [
      { to: '/admin/targets', label: '分区与信源' },
      { to: '/admin/proxies', label: '采集代理' },
      { to: '/admin/customs', label: '用户监控' },
    ],
  },
  {
    key: 'content',
    label: '内容与数据',
    items: [
      { to: '/admin/contents', label: '内容管理' },
      { to: '/admin/events', label: '事件审核' },
      { to: '/admin/products', label: '商品管理' },
      { to: '/admin/rules', label: '分类词典' },
      { to: '/admin/aihot', label: 'AI 资讯' },
    ],
  },
  {
    key: 'system',
    label: '用户与系统',
    items: [
      { to: '/admin/users', label: '用户管理' },
      { to: '/admin/bot', label: '机器人通知' },
      { to: '/admin/ai', label: 'AI 配置' },
      { to: '/admin/settings', label: '站点设置' },
    ],
  },
] as const
