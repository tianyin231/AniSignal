/**
 * 游戏分区（鸣潮 / 碧蓝航线）：公开站大标签页切换的单一状态源。
 *
 * 规则：
 * - URL query `?community=` 优先（分享链接与刷新后保持分区一致；默认分区不写进 URL 保持干净）；
 * - 无 query 时回退到 localStorage 记忆（客户端挂载后生效，避免 SSR 水合不一致）；
 * - 切换分区时重置页码，并把记忆写入 localStorage。
 * 分区清单与后端 site_config.communities / COMMUNITY_LABELS 保持一致。
 *
 * 分区视觉（色点/徽章/顶栏强调条）与"分区→可用平台"映射都收敛在本文件：
 * 新增分区 = 在 GAME_COMMUNITIES 加一条（含 color）+ 在 COMMUNITY_CLASSES/平台映射补对应项。
 */

export interface GameCommunity {
  key: 'wuwa' | 'blhx' | 'hsr' | 'arknights' | 'ai'
  label: string
  /** 频道头图上的英文名 */
  en: string
  /** 分区语义色键，映射 COMMUNITY_CLASSES 的 Tailwind 类组（不用动态拼接，避免被 purge） */
  color: 'yellow' | 'sky' | 'violet' | 'orange' | 'teal'
}

// 分区主题色对齐各游戏品牌色：鸣潮金 / 碧蓝天蓝 / 星穹铁道紫 / 明日方舟橙
export const GAME_COMMUNITIES: GameCommunity[] = [
  { key: 'wuwa', label: '鸣潮', en: 'Wuthering Waves', color: 'yellow' },
  { key: 'blhx', label: '碧蓝航线', en: 'Azur Lane', color: 'sky' },
  { key: 'hsr', label: '崩坏星穹铁道', en: 'Honkai: Star Rail', color: 'violet' },
  { key: 'arknights', label: '明日方舟', en: 'Arknights', color: 'orange' },
  { key: 'ai', label: 'AI', en: 'AI News', color: 'teal' },
]

export type GameCommunityKey = GameCommunity['key']
const DEFAULT_COMMUNITY: GameCommunityKey = 'wuwa'
const STORAGE_KEY = 'anisignal-community'
const VALID_KEYS = new Set(GAME_COMMUNITIES.map(item => item.key))

/**
 * 分区色 → Tailwind 类组（全部字面量写出，只保留实际消费的键）。
 * 色值来自 main.css 的信号台令牌（--sd-<分区>），深色模式自动切换。
 * chip：分区徽章；nav：顶栏频道选中态（正文色 + 分区色下划线）；dot：分区色点 / 色块；text：分区色文字。
 */
const COMMUNITY_CLASSES: Record<
  GameCommunity['color'] | 'blue',
  { chip: string, nav: string, dot: string, text: string }
> = {
  // blue = 站点主色：非分区频道（首页/定制化）与兜底用
  blue: { chip: 'bg-brand-soft text-brand-ink', nav: 'border-brand text-ink', dot: 'bg-brand', text: 'text-brand-ink' },
  yellow: { chip: 'bg-wuwa-soft text-wuwa-ink', nav: 'border-wuwa text-ink', dot: 'bg-wuwa', text: 'text-wuwa-ink' },
  sky: { chip: 'bg-blhx-soft text-blhx-ink', nav: 'border-blhx text-ink', dot: 'bg-blhx', text: 'text-blhx-ink' },
  violet: { chip: 'bg-hsr-soft text-hsr-ink', nav: 'border-hsr text-ink', dot: 'bg-hsr', text: 'text-hsr-ink' },
  orange: { chip: 'bg-arknights-soft text-arknights-ink', nav: 'border-arknights text-ink', dot: 'bg-arknights', text: 'text-arknights-ink' },
  teal: { chip: 'bg-ai-soft text-ai-ink', nav: 'border-ai text-ink', dot: 'bg-ai', text: 'text-ai-ink' },
}

export function communityClasses(color: string) {
  return COMMUNITY_CLASSES[color as GameCommunity['color'] | 'blue'] ?? COMMUNITY_CLASSES.blue
}

/** 分区 key → 元数据（未知 key 返回 undefined）。 */
export function communityMeta(key?: string | null): GameCommunity | undefined {
  return GAME_COMMUNITIES.find(c => c.key === key)
}

/** 频道主题类：挂在布局根节点上，把 accent 令牌切成该分区的品牌色（main.css .ch-*）。 */
export function channelThemeClass(key?: string | null): string {
  return communityMeta(key) ? `ch-${key}` : ''
}

/** 各分区可用的平台筛选（/api/contents 的 platform 参数值）。 */
export const PLATFORMS_BY_COMMUNITY: Record<GameCommunityKey, string[]> = {
  wuwa: ['bilibili', 'youtube', 'kuro'],
  blhx: ['bilibili'],
  hsr: ['bilibili', 'youtube'],
  arknights: ['bilibili', 'youtube'],
  ai: ['bilibili', 'x', 'rss'],
}

export function platformsForCommunity(community: string): string[] {
  if (community === 'all') {
    // 聚合视角：全部分区平台的并集
    return [...new Set(Object.values(PLATFORMS_BY_COMMUNITY).flat())]
  }
  return PLATFORMS_BY_COMMUNITY[community as GameCommunityKey] ?? ['bilibili']
}

/**
 * 栏目页的分区视角：URL 显式带 community=all 时为跨分区聚合（首页"首页"频道进入），
 * 否则跟随分区状态源（URL 分区 → localStorage → 默认）。
 */
export function useCommunityScope() {
  const route = useRoute()
  const { community } = useGameSection()
  const scope = computed<string>(() => {
    const query = String(route.query.community || '')
    return query === 'all' ? 'all' : community.value
  })
  return { scope, isAll: computed(() => scope.value === 'all') }
}

type PageCommunity = { path: string, key: GameCommunityKey } | null

/**
 * 详情页声明"本页内容属于哪个分区"（内容 / 事件详情；商品详情会把分区重定向进 URL，无需声明）。
 * 这类页面 URL 里通常没有 community，若不声明，频道高亮会回退到上次浏览的分区（曾把 AI 内容标成鸣潮）。
 */
export function usePageCommunity(source: () => string | null | undefined) {
  const route = useRoute()
  const state = useState<PageCommunity>('page-community', () => null)
  watch(source, (key) => {
    state.value = key && VALID_KEYS.has(key as GameCommunityKey) ? { path: route.path, key: key as GameCommunityKey } : null
  }, { immediate: true })
}

/**
 * 当前高亮的频道：聚合首页 → home；分区首页 → 该分区；/ai → ai；定制化 → custom；
 * 详情页优先用 usePageCommunity 声明的分区；聚合视角（community=all）不高亮任何分区。
 * 顶栏选中态与布局的频道主题色（accent）都以此为准。
 */
export function useActiveChannel() {
  const route = useRoute()
  const { community } = useGameSection()
  const pageCommunity = useState<PageCommunity>('page-community', () => null)
  // 顶栏与布局先于页面的异步 setup 渲染，SSR 时还不知道详情内容属于哪个分区；
  // 详情页在挂载前不高亮任何频道，挂载后再切到页面声明的分区（避免先闪出错误分区）。
  const mounted = useState('active-channel-mounted', () => false)
  if (import.meta.client) onMounted(() => { mounted.value = true })
  const active = computed<string | null>(() => {
    if (route.path === '/ai' || route.path.startsWith('/ai/')) return 'ai'
    if (route.path.startsWith('/custom')) return 'custom'
    const query = String(route.query.community || '')
    const queryKey = VALID_KEYS.has(query as GameCommunityKey) ? query : null
    if (route.path === '/') return queryKey ?? 'home'
    if (DETAIL_PATH.test(route.path)) {
      if (!mounted.value) return null
      if (pageCommunity.value?.path === route.path) return pageCommunity.value.key
    }
    if (queryKey) return queryKey
    if (query === 'all' || route.path === '/calendar') return null
    return community.value
  })
  return { active }
}

/** 内容 / 事件详情页：URL 不带分区，由页面 usePageCommunity 声明 */
const DETAIL_PATH = /^\/(contents|events)\/[^/]+$/

export function useGameSection() {
  const route = useRoute()
  const router = useRouter()
  const stored = useState<GameCommunityKey>('game-section-stored', () => DEFAULT_COMMUNITY)
  const hydrated = useState('game-section-hydrated', () => false)

  const community = computed<GameCommunityKey>(() => {
    if (route.path === '/ai' || route.path.startsWith('/ai/')) return 'ai'
    const query = String(route.query.community || '')
    if (VALID_KEYS.has(query)) return query as GameCommunityKey
    return hydrated.value ? stored.value : DEFAULT_COMMUNITY
  })

  /** 当前分区的完整元数据（label/color），供品牌徽章、强调条等消费。 */
  const currentCommunity = computed<GameCommunity>(
    () => GAME_COMMUNITIES.find(item => item.key === community.value) ?? GAME_COMMUNITIES[0],
  )

  const communityQuery = computed(() =>
    community.value === DEFAULT_COMMUNITY ? {} : { community: community.value },
  )

  function hydrate() {
    if (hydrated.value) return
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved && VALID_KEYS.has(saved)) stored.value = saved as GameCommunityKey
    }
    catch { /* 隐私模式等场景下静默回退默认分区 */ }
    hydrated.value = true
  }

  function rememberCommunity(key: string) {
    if (!VALID_KEYS.has(key)) return
    stored.value = key as GameCommunityKey
    hydrated.value = true
    try { localStorage.setItem(STORAGE_KEY, key) }
    catch { /* 存不进就只在当前会话生效 */ }
  }

  /** 由顶栏在挂载和路由变化时调用，避免每个分区消费者各自监听路由。 */
  function rememberRouteCommunity() {
    if (!import.meta.client) return
    const key = route.path === '/ai' ? 'ai' : String(route.query.community || '')
    rememberCommunity(key)
  }

  async function setCommunity(key: string) {
    if (!VALID_KEYS.has(key)) return
    rememberCommunity(key)
    await router.replace({
      query: {
        ...route.query,
        community: key === DEFAULT_COMMUNITY ? undefined : key,
        page: undefined,
      },
    })
  }

  /**
   * 顶栏频道语义：点击分区频道 = 记住分区并前往该分区的首页。
   * 频道入口始终显式写 community（即使默认分区），与跨分区聚合首页 `/` 区分。
   */
  async function selectCommunity(key: string) {
    if (!VALID_KEYS.has(key)) return
    rememberCommunity(key)
    await router.push(key === 'ai' ? { path: '/ai' } : { path: '/', query: { community: key } })
  }

  if (import.meta.client) onMounted(hydrate)

  return { community, currentCommunity, communityQuery, rememberRouteCommunity, setCommunity, selectCommunity, communities: GAME_COMMUNITIES }
}
