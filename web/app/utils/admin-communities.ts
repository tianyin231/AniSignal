/**
 * 管理端分区选项的单一来源（与 composables/useGameSection 的 GAME_COMMUNITIES 保持一致）。
 * 新增分区时在这里加一条即可让目标/内容/事件/商品/词典各管理页的选项与徽章同步。
 */

export const ADMIN_COMMUNITIES = [
  { key: 'wuwa', label: '鸣潮', short: '鸣潮' },
  { key: 'blhx', label: '碧蓝航线', short: '碧蓝' },
  { key: 'hsr', label: '崩坏星穹铁道', short: '星穹' },
  { key: 'arknights', label: '明日方舟', short: '方舟' },
] as const

export const ADMIN_COMMUNITY_LABELS: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.label]),
)

export const ADMIN_COMMUNITY_SHORT: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.short]),
)

/** 行内分区徽章配色（slate=默认分区 / sky=碧蓝 / violet=星铁 / orange=方舟）。 */
export const ADMIN_COMMUNITY_BADGE: Record<string, string> = {
  wuwa: 'bg-slate-500/10 text-slate-600 dark:text-zinc-300',
  blhx: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  hsr: 'bg-violet-500/10 text-violet-700 dark:text-violet-300',
  arknights: 'bg-orange-500/10 text-orange-700 dark:text-orange-300',
}
