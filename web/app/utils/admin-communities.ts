/**
 * 管理端分区选项的单一来源（与 composables/useGameSection 的 GAME_COMMUNITIES 保持一致）。
 * 新增分区时在这里加一条即可让目标/内容/事件/商品/词典各管理页的选项与徽章同步。
 */

export const ADMIN_COMMUNITIES = [
  { key: 'wuwa', label: '鸣潮', short: '鸣潮' },
  { key: 'blhx', label: '碧蓝航线', short: '碧蓝' },
  { key: 'hsr', label: '崩坏星穹铁道', short: '星穹' },
] as const

export const ADMIN_COMMUNITY_LABELS: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.label]),
)

/** 行内分区徽章配色（slate=默认分区 / cyan=碧蓝航线）。 */
export const ADMIN_COMMUNITY_BADGE: Record<string, string> = {
  wuwa: 'bg-slate-500/10 text-slate-600 dark:text-zinc-300',
  blhx: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
  hsr: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
}
