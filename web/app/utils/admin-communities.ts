/**
 * 管理端分区选项（同步支持内置分区与自定义分区）。
 */
import type { PublicSiteConfig, SiteCommunityItem } from '~/composables/useSiteConfig'
import { hexCommunityClasses, parseHex6 } from '~/utils/community-palette'

export const ADMIN_COMMUNITIES = [
  { key: 'wuwa', label: '鸣潮', short: '鸣潮', color: 'yellow' },
  { key: 'blhx', label: '碧蓝航线', short: '碧蓝', color: 'sky' },
  { key: 'hsr', label: '崩坏星穹铁道', short: '星穹', color: 'violet' },
  { key: 'arknights', label: '明日方舟', short: '方舟', color: 'orange' },
  { key: 'genshin', label: '原神', short: '原神', color: 'cyan' },
  { key: 'endfield', label: '终末地', short: '终末地', color: 'lime' },
  { key: 'zzz', label: '绝区零', short: '绝区零', color: 'orange' },
  { key: 'petitplanet', label: '星布谷地', short: '星布谷地', color: 'teal' },
  { key: 'hna', label: '崩坏：因缘精灵', short: '因缘精灵', color: 'violet' },
  { key: 'roco', label: '洛克王国：世界', short: '洛克世界', color: 'sky' },
  { key: 'promilia', label: '蓝色星原：旅谣', short: '蓝色星原', color: 'cyan' },
  { key: 'nte', label: '异环', short: '异环', color: 'yellow' },
  { key: 'nikke', label: 'NIKKE', short: 'NIKKE', color: 'orange' },
  { key: 'infinitynikki', label: '无限暖暖', short: '暖暖', color: 'violet' },
  { key: 'ai', label: 'AI', short: 'AI', color: 'teal' },
] as const

export const ADMIN_COMMUNITY_LABELS: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.label]),
)

export const ADMIN_COMMUNITY_SHORT: Record<string, string> = Object.fromEntries(
  ADMIN_COMMUNITIES.map(c => [c.key, c.short]),
)

const COLOR_BADGES: Record<string, string> = {
  blue: 'bg-blue-500/10 text-blue-700 dark:text-blue-300',
  yellow: 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-300',
  sky: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  violet: 'bg-violet-500/10 text-violet-700 dark:text-violet-300',
  orange: 'bg-orange-500/10 text-orange-700 dark:text-orange-300',
  cyan: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
  lime: 'bg-lime-500/10 text-lime-700 dark:text-lime-300',
  teal: 'bg-teal-500/10 text-teal-700 dark:text-teal-300',
}

export const ADMIN_COMMUNITY_BADGE: Record<string, string> = {
  wuwa: 'bg-slate-500/10 text-slate-600 dark:text-zinc-300',
  blhx: COLOR_BADGES.sky!,
  hsr: COLOR_BADGES.violet!,
  arknights: COLOR_BADGES.orange!,
  genshin: COLOR_BADGES.cyan!,
  endfield: COLOR_BADGES.lime!,
  zzz: COLOR_BADGES.orange!,
  petitplanet: COLOR_BADGES.teal!,
  hna: COLOR_BADGES.violet!,
  roco: COLOR_BADGES.sky!,
  promilia: COLOR_BADGES.cyan!,
  nte: COLOR_BADGES.yellow!,
  nikke: COLOR_BADGES.orange!,
  infinitynikki: COLOR_BADGES.violet!,
  ai: COLOR_BADGES.teal!,
}

export function useAdminCommunities() {
  const { data: siteConfig } = useNuxtData<PublicSiteConfig>('site-config')
  const refreshSiteConfig = () => refreshNuxtData('site-config')

  const communities = computed<SiteCommunityItem[]>(() => {
    if (siteConfig.value?.all_communities?.length) {
      return siteConfig.value.all_communities
    }
    return ADMIN_COMMUNITIES.map(c => ({
      key: c.key,
      label: c.label,
      en: c.key,
      color: c.color,
      domain: c.key === 'ai' ? 'news' : 'game',
      platforms: [],
      active: true,
      builtin: true,
    }))
  })

  const activeCommunities = computed(() => communities.value.filter(c => c.active))

  const labelOf = (key?: string | null): string => {
    if (!key) return '—'
    return communities.value.find(c => c.key === key)?.label ?? ADMIN_COMMUNITY_LABELS[key] ?? key
  }

  const shortOf = (key?: string | null): string => {
    if (!key) return '—'
    return ADMIN_COMMUNITY_SHORT[key] ?? communities.value.find(c => c.key === key)?.label ?? key
  }

  const defaultColors: Record<string, string> = Object.fromEntries(ADMIN_COMMUNITIES.map(c => [c.key, c.color]))

  const badgeOf = (key?: string | null): string => {
    if (!key) return 'bg-chip text-muted'
    const item = communities.value.find(c => c.key === key)
    if (item) {
      const hex6 = parseHex6(item.color)
      if (hex6) return hexCommunityClasses(hex6).chip
      if (ADMIN_COMMUNITY_BADGE[key] && item.color === defaultColors[key]) return ADMIN_COMMUNITY_BADGE[key]!
      if (COLOR_BADGES[item.color]) return COLOR_BADGES[item.color]!
    }
    return ADMIN_COMMUNITY_BADGE[key] || 'bg-chip text-muted'
  }

  return { communities, activeCommunities, labelOf, shortOf, badgeOf, refreshSiteConfig }
}
