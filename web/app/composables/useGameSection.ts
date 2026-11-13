/**
 * 分区状态源（内置分区 + 管理端自定义分区）：公开站大标签页切换的单一状态源。
 *
 * 规则：
 * - 优先从 site-config 读取当前启用的分区列表（含自定义分区与内置分区显隐状态）；
 * - URL query `?community=` 优先；无 query 时回退到 localStorage 记忆；
 * - 默认分区若已被管理员隐藏，自动落到首个启用的分区。
 */

import type { PublicSiteConfig } from '~/composables/useSiteConfig'

export type CommunityColor = 'blue' | 'yellow' | 'sky' | 'violet' | 'orange' | 'cyan' | 'lime' | 'teal'

export interface GameCommunity {
  key: string
  label: string
  /** 频道头图上的英文名 */
  en: string
  /** 分区语义色键，映射 COMMUNITY_CLASSES 的 Tailwind 类组（不用动态拼接，避免被 purge） */
  color: CommunityColor
  domain?: 'game' | 'news'
  platforms?: string[]
  active?: boolean
  builtin?: boolean
}

// 内置分区兜底列表（后端 site-config 未返回时使用）
export const GAME_COMMUNITIES: GameCommunity[] = [
  { key: 'wuwa', label: '鸣潮', en: 'Wuthering Waves', color: 'yellow', domain: 'game', builtin: true, active: true },
  { key: 'blhx', label: '碧蓝航线', en: 'Azur Lane', color: 'sky', domain: 'game', builtin: true, active: true },
  { key: 'hsr', label: '崩坏星穹铁道', en: 'Honkai: Star Rail', color: 'violet', domain: 'game', builtin: true, active: true },
  { key: 'arknights', label: '明日方舟', en: 'Arknights', color: 'orange', domain: 'game', builtin: true, active: true },
  { key: 'genshin', label: '原神', en: 'Genshin Impact', color: 'cyan', domain: 'game', builtin: true, active: true },
  { key: 'endfield', label: '终末地', en: 'Arknights: Endfield', color: 'lime', domain: 'game', builtin: true, active: true },
  { key: 'zzz', label: '绝区零', en: 'Zenless Zone Zero', color: 'orange', domain: 'game', builtin: true, active: true },
  { key: 'petitplanet', label: '星布谷地', en: 'Petit Planet', color: 'teal', domain: 'game', builtin: true, active: true },
  { key: 'hna', label: '崩坏：因缘精灵', en: 'Honkai: Nexus Anima', color: 'violet', domain: 'game', builtin: true, active: true },
  { key: 'roco', label: '洛克王国：世界', en: 'Roco Kingdom', color: 'sky', domain: 'game', builtin: true, active: true },
  { key: 'promilia', label: '蓝色星原：旅谣', en: 'Azur Promilia', color: 'cyan', domain: 'game', builtin: true, active: true },
  { key: 'nte', label: '异环', en: 'Neverness to Everness', color: 'yellow', domain: 'game', builtin: true, active: true },
  { key: 'nikke', label: 'NIKKE', en: 'Goddess of Victory: NIKKE', color: 'orange', domain: 'game', builtin: true, active: true },
  { key: 'infinitynikki', label: '无限暖暖', en: 'Infinity Nikki', color: 'violet', domain: 'game', builtin: true, active: true },
  { key: 'ai', label: 'AI', en: 'AI News', color: 'teal', domain: 'news', builtin: true, active: true },
]

export type GameCommunityKey = string
const DEFAULT_COMMUNITY = 'wuwa'
const STORAGE_KEY = 'anisignal-community'
const BUILTIN_KEYS = new Set(GAME_COMMUNITIES.map(item => item.key))

const COLOR_TO_THEME_CLASS: Record<CommunityColor, string> = {
  blue: '',
  yellow: 'ch-wuwa',
  sky: 'ch-blhx',
  violet: 'ch-hsr',
  orange: 'ch-arknights',
  cyan: 'ch-genshin',
  lime: 'ch-endfield',
  teal: 'ch-ai',
}

function readSiteCommunities(): { active: GameCommunity[]; all: GameCommunity[] } {
  try {
    const { data } = useNuxtData<PublicSiteConfig>('site-config')
    const all = data.value?.all_communities?.length ? data.value.all_communities : GAME_COMMUNITIES
    const active = data.value?.communities !== undefined
      ? (data.value.communities.length ? data.value.communities : all.slice(0, 1))
      : GAME_COMMUNITIES
    return { active, all }
  } catch {
    return { active: GAME_COMMUNITIES, all: GAME_COMMUNITIES }
  }
}

const COMMUNITY_CLASSES: Record<
  CommunityColor,
  { chip: string; nav: string; dot: string; text: string }
> = {
  blue: { chip: 'bg-brand-soft text-brand-ink', nav: 'border-brand text-ink', dot: 'bg-brand', text: 'text-brand-ink' },
  yellow: { chip: 'bg-wuwa-soft text-wuwa-ink', nav: 'border-wuwa text-ink', dot: 'bg-wuwa', text: 'text-wuwa-ink' },
  sky: { chip: 'bg-blhx-soft text-blhx-ink', nav: 'border-blhx text-ink', dot: 'bg-blhx', text: 'text-blhx-ink' },
  violet: { chip: 'bg-hsr-soft text-hsr-ink', nav: 'border-hsr text-ink', dot: 'bg-hsr', text: 'text-hsr-ink' },
  orange: { chip: 'bg-arknights-soft text-arknights-ink', nav: 'border-arknights text-ink', dot: 'bg-arknights', text: 'text-arknights-ink' },
  cyan: { chip: 'bg-genshin-soft text-genshin-ink', nav: 'border-genshin text-ink', dot: 'bg-genshin', text: 'text-genshin-ink' },
  lime: { chip: 'bg-endfield-soft text-endfield-ink', nav: 'border-endfield text-ink', dot: 'bg-endfield', text: 'text-endfield-ink' },
  teal: { chip: 'bg-ai-soft text-ai-ink', nav: 'border-ai text-ink', dot: 'bg-ai', text: 'text-ai-ink' },
}

export function communityClasses(color: string) {
  return COMMUNITY_CLASSES[color as CommunityColor] ?? COMMUNITY_CLASSES.blue
}

/** 分区 key → 元数据（支持自定义分区；未知 key 返回 undefined）。 */
export function communityMeta(key?: string | null): GameCommunity | undefined {
  if (!key) return undefined
  const { all } = readSiteCommunities()
  return all.find(c => c.key === key) ?? GAME_COMMUNITIES.find(c => c.key === key)
}

/** 频道主题类：内置分区走专属 .ch-*，自定义分区按颜色映射对应品牌色令牌。 */
export function channelThemeClass(key?: string | null): string {
  const meta = communityMeta(key)
  if (!meta) return ''
  if (BUILTIN_KEYS.has(meta.key)) return `ch-${meta.key}`
  return COLOR_TO_THEME_CLASS[meta.color] ?? ''
}

export const PLATFORMS_BY_COMMUNITY: Record<string, string[]> = {
  wuwa: ['bilibili', 'youtube', 'kuro'],
  blhx: ['bilibili'],
  hsr: ['bilibili', 'youtube'],
  arknights: ['bilibili', 'youtube'],
  genshin: ['bilibili', 'youtube', 'game_official', 'x', 'miyoushe'],
  endfield: ['bilibili', 'youtube', 'game_official', 'x'],
  zzz: ['bilibili'],
  petitplanet: ['bilibili'],
  hna: ['bilibili'],
  roco: ['bilibili'],
  promilia: ['bilibili'],
  nte: ['bilibili'],
  nikke: ['game_official', 'youtube', 'x'],
  infinitynikki: ['bilibili'],
  ai: ['bilibili', 'x', 'rss'],
}

function normalizeFilterPlatforms(raw: string[]): string[] {
  return [...new Set(raw.map(p => (p === 'kuro_cn' || p === 'kuro_global' ? 'kuro' : p)))]
}

export function platformsForCommunity(community: string): string[] {
  const { active } = readSiteCommunities()
  if (community === 'all') {
    const dynamicAll = active.flatMap(c => c.platforms?.length ? normalizeFilterPlatforms(c.platforms) : (PLATFORMS_BY_COMMUNITY[c.key] ?? ['bilibili']))
    return [...new Set([...dynamicAll, ...Object.values(PLATFORMS_BY_COMMUNITY).flat()])]
  }
  const meta = communityMeta(community)
  if (meta?.platforms?.length) return normalizeFilterPlatforms(meta.platforms)
  return PLATFORMS_BY_COMMUNITY[community] ?? ['bilibili', 'youtube', 'rss', 'x']
}

export function useCommunityScope({ gameOnly = false } = {}) {
  const route = useRoute()
  const { community } = useGameSection()
  const scope = computed<string>(() => {
    const query = String(route.query.community || '')
    if (query === 'all' || (gameOnly && community.value === 'ai')) return 'all'
    return community.value
  })
  return { scope, isAll: computed(() => scope.value === 'all') }
}

type PageCommunity = { path: string; key: string } | null

export function usePageCommunity(source: () => string | null | undefined) {
  const route = useRoute()
  const state = useState<PageCommunity>('page-community', () => null)
  watch(source, (key) => {
    state.value = key && communityMeta(key) ? { path: route.path, key } : null
  }, { immediate: true })
}

export function useActiveChannel() {
  const route = useRoute()
  const { community } = useGameSection()
  const pageCommunity = useState<PageCommunity>('page-community', () => null)
  const mounted = useState('active-channel-mounted', () => false)
  if (import.meta.client) onMounted(() => { mounted.value = true })
  const active = computed<string | null>(() => {
    if (route.path === '/ai' || route.path.startsWith('/ai/')) return 'ai'
    if (route.path.startsWith('/custom')) return 'custom'
    if (['/sources', '/stats'].includes(route.path)) return 'home'
    if (['/account', '/watch', '/project'].includes(route.path)) return null
    const query = String(route.query.community || '')
    const queryKey = query && communityMeta(query) ? query : null
    if (route.path === '/') return queryKey ?? 'home'
    if (DETAIL_PATH.test(route.path)) {
      if (!mounted.value) return null
      if (pageCommunity.value?.path === route.path) return pageCommunity.value.key
    }
    if (queryKey) return queryKey
    if (query === 'all' || route.path === '/calendar') return null
    if (community.value === 'ai' && GAME_ONLY_PATH.test(route.path)) return null
    return community.value
  })
  return { active }
}

const GAME_ONLY_PATH = /^\/(events|products)(\/|$)/
const DETAIL_PATH = /^\/(contents|events)\/[^/]+$/

export const HIDDEN_COMMUNITIES_COOKIE = 'anisignal_hidden'

export function useHiddenCommunities() {
  const raw = useCookie<string | null | undefined>(HIDDEN_COMMUNITIES_COOKIE, { readonly: true, decode: value => value })
  const user = useState<unknown>('auth-user', () => null)
  const loaded = useState<boolean>('auth-loaded', () => false)
  const hidden = computed(() => {
    if (loaded.value && !user.value) return new Set<string>()
    return new Set(String(raw.value || '').split('.').filter(key => Boolean(communityMeta(key))))
  })
  return { hidden }
}

export function useGameSection() {
  const route = useRoute()
  const router = useRouter()
  const { data: siteConfig } = useNuxtData<PublicSiteConfig>('site-config')
  const communities = computed<GameCommunity[]>(() => {
    const list = siteConfig.value?.communities
    if (list !== undefined) return list.length ? list : (siteConfig.value?.all_communities?.slice(0, 1) ?? GAME_COMMUNITIES.slice(0, 1))
    return GAME_COMMUNITIES
  })
  const allCommunities = computed<GameCommunity[]>(() =>
    siteConfig.value?.all_communities?.length ? siteConfig.value.all_communities : GAME_COMMUNITIES,
  )
  const validKeys = computed(() => new Set(allCommunities.value.map(item => item.key)))
  const fallbackKey = computed(() =>
    communities.value.find(c => c.key === DEFAULT_COMMUNITY)?.key
    ?? communities.value.find(c => c.key !== 'ai')?.key
    ?? communities.value[0]?.key
    ?? DEFAULT_COMMUNITY,
  )

  const stored = useState<string>('game-section-stored', () => DEFAULT_COMMUNITY)
  const hydrated = useState('game-section-hydrated', () => false)
  const { hidden } = useHiddenCommunities()

  const unhidden = (key: string): string => {
    const activeSet = new Set(communities.value.map(c => c.key))
    if (!activeSet.has(key) || hidden.value.has(key)) {
      return communities.value.find(item => item.key !== 'ai' && !hidden.value.has(item.key))?.key
        ?? fallbackKey.value
    }
    return key
  }

  const community = computed<string>(() => {
    if (route.path === '/ai' || route.path.startsWith('/ai/')) return 'ai'
    const query = String(route.query.community || '')
    if (validKeys.value.has(query)) return query
    return unhidden(hydrated.value ? stored.value : fallbackKey.value)
  })

  const currentCommunity = computed<GameCommunity>(
    () => allCommunities.value.find(item => item.key === community.value) ?? communities.value[0] ?? GAME_COMMUNITIES[0]!,
  )

  const omitFromUrl = (key: string) => key === DEFAULT_COMMUNITY && fallbackKey.value === DEFAULT_COMMUNITY && !hidden.value.has(key)
  const communityQuery = computed(() =>
    omitFromUrl(community.value) ? {} : { community: community.value },
  )

  function hydrate() {
    if (hydrated.value) return
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved && validKeys.value.has(saved)) stored.value = saved
    } catch { /* 隐私模式等场景下静默回退默认分区 */ }
    hydrated.value = true
  }

  function rememberCommunity(key: string) {
    if (!validKeys.value.has(key)) return
    stored.value = key
    hydrated.value = true
    try { localStorage.setItem(STORAGE_KEY, key) } catch { /* 存不进就只在当前会话生效 */ }
  }

  function rememberRouteCommunity() {
    if (!import.meta.client) return
    const key = route.path === '/ai' ? 'ai' : String(route.query.community || '')
    rememberCommunity(key)
  }

  async function setCommunity(key: string) {
    if (!validKeys.value.has(key)) return
    rememberCommunity(key)
    await router.replace({
      query: {
        ...route.query,
        community: omitFromUrl(key) ? undefined : key,
        page: undefined,
      },
    })
  }

  async function selectCommunity(key: string) {
    if (!validKeys.value.has(key)) return
    rememberCommunity(key)
    await router.push(key === 'ai' ? { path: '/ai' } : { path: '/', query: { community: key } })
  }

  if (import.meta.client) onMounted(hydrate)

  return {
    community,
    currentCommunity,
    communityQuery,
    rememberRouteCommunity,
    setCommunity,
    selectCommunity,
    communities,
    allCommunities,
  }
}
