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
  /** 分区语义色键，映射 COMMUNITY_CLASSES 的 Tailwind 类组（不用动态拼接，避免被 purge） */
  color: 'yellow' | 'sky' | 'violet' | 'orange' | 'teal'
}

// 分区主题色对齐各游戏品牌色：鸣潮金 / 碧蓝天蓝 / 星穹铁道紫 / 明日方舟橙
export const GAME_COMMUNITIES: GameCommunity[] = [
  { key: 'wuwa', label: '鸣潮', color: 'yellow' },
  { key: 'blhx', label: '碧蓝航线', color: 'sky' },
  { key: 'hsr', label: '崩坏星穹铁道', color: 'violet' },
  { key: 'arknights', label: '明日方舟', color: 'orange' },
  { key: 'ai', label: 'AI', color: 'teal' },
]

export type GameCommunityKey = GameCommunity['key']
const DEFAULT_COMMUNITY: GameCommunityKey = 'wuwa'
const STORAGE_KEY = 'anisignal-community'
const VALID_KEYS = new Set(GAME_COMMUNITIES.map(item => item.key))

/**
 * 分区色 → Tailwind 类组（全部字面量写出，只保留实际消费的键）。
 * chip：内容行/事件行/轮播卡的分区徽章；nav：顶栏频道 tab 选中态（分区色文字+下划线）。
 */
const COMMUNITY_CLASSES: Record<
  GameCommunity['color'] | 'blue',
  { chip: string, nav: string }
> = {
  // blue = 站点主色：非分区频道（首页/定制化）与兜底用
  blue: {
    chip: 'bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300',
    nav: 'border-blue-500 text-blue-700 dark:text-blue-300',
  },
  yellow: {
    chip: 'bg-yellow-500/15 text-yellow-700 dark:bg-yellow-400/15 dark:text-yellow-300',
    nav: 'border-yellow-500 text-yellow-700 dark:text-yellow-300',
  },
  sky: {
    chip: 'bg-sky-500/10 text-sky-700 dark:bg-sky-400/15 dark:text-sky-300',
    nav: 'border-sky-500 text-sky-700 dark:text-sky-300',
  },
  violet: {
    chip: 'bg-violet-500/10 text-violet-700 dark:bg-violet-400/15 dark:text-violet-300',
    nav: 'border-violet-500 text-violet-700 dark:text-violet-300',
  },
  orange: {
    chip: 'bg-orange-500/10 text-orange-700 dark:bg-orange-400/15 dark:text-orange-300',
    nav: 'border-orange-500 text-orange-700 dark:text-orange-300',
  },
  teal: {
    chip: 'bg-teal-500/10 text-teal-700 dark:text-teal-300',
    nav: 'border-teal-500 text-teal-700 dark:text-teal-300',
  },
}

export function communityClasses(color: string) {
  return COMMUNITY_CLASSES[color as GameCommunity['color'] | 'blue'] ?? COMMUNITY_CLASSES.blue
}

/** 各分区可用的平台筛选（/api/contents 的 platform 参数值）。 */
export const PLATFORMS_BY_COMMUNITY: Record<GameCommunityKey, string[]> = {
  wuwa: ['bilibili', 'youtube', 'kuro'],
  blhx: ['bilibili'],
  hsr: ['bilibili', 'youtube'],
  arknights: ['bilibili', 'youtube'],
  ai: ['bilibili'],
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

export function useGameSection() {
  const route = useRoute()
  const router = useRouter()
  const stored = useState<GameCommunityKey>('game-section-stored', () => DEFAULT_COMMUNITY)
  const hydrated = useState('game-section-hydrated', () => false)

  const community = computed<GameCommunityKey>(() => {
    if (route.path === '/ai') return 'ai'
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

  async function setCommunity(key: string) {
    if (!VALID_KEYS.has(key)) return
    stored.value = key as GameCommunityKey
    hydrated.value = true
    try { localStorage.setItem(STORAGE_KEY, key) }
    catch { /* 存不进就只在当前会话生效 */ }
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
    stored.value = key as GameCommunityKey
    hydrated.value = true
    try { localStorage.setItem(STORAGE_KEY, key) }
    catch { /* 存不进就只在当前会话生效 */ }
    await router.push(key === 'ai' ? { path: '/ai' } : { path: '/', query: { community: key } })
  }

  if (import.meta.client) onMounted(hydrate)

  return { community, currentCommunity, communityQuery, setCommunity, selectCommunity, communities: GAME_COMMUNITIES }
}
