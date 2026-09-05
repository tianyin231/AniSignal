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
  key: 'wuwa' | 'blhx'
  label: string
  /** 分区语义色键，映射 COMMUNITY_CLASSES 的 Tailwind 类组（不用动态拼接，避免被 purge） */
  color: 'blue' | 'cyan'
}

export const GAME_COMMUNITIES: GameCommunity[] = [
  { key: 'wuwa', label: '鸣潮', color: 'blue' },
  { key: 'blhx', label: '碧蓝航线', color: 'cyan' },
]

export type GameCommunityKey = GameCommunity['key']
const DEFAULT_COMMUNITY: GameCommunityKey = 'wuwa'
const STORAGE_KEY = 'anisignal-community'
const VALID_KEYS = new Set(GAME_COMMUNITIES.map(item => item.key))

/**
 * 分区色 → Tailwind 类组（全部字面量写出）。
 * bar 是分区条底部强调条的发光渐变；chip 用于分区徽章；nav 是顶栏频道 tab 的
 * 选中态（分区色文字 + 2px 下划线）；tabActive 是细条分段控件选中态。
 */
const COMMUNITY_CLASSES: Record<
  GameCommunity['color'],
  { dot: string, text: string, chip: string, bar: string, nav: string, tabActive: string }
> = {
  blue: {
    dot: 'bg-blue-500',
    text: 'text-blue-600 dark:text-blue-300',
    chip: 'bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300',
    bar: 'from-blue-500/80 via-blue-400/60 to-cyan-400/40',
    nav: 'border-blue-500 text-blue-700 dark:text-blue-300',
    tabActive: 'bg-white text-blue-700 shadow-sm dark:bg-zinc-900 dark:text-blue-300',
  },
  cyan: {
    dot: 'bg-cyan-500',
    text: 'text-cyan-700 dark:text-cyan-300',
    chip: 'bg-cyan-500/10 text-cyan-700 dark:bg-cyan-400/15 dark:text-cyan-300',
    bar: 'from-cyan-500/80 via-cyan-400/60 to-sky-400/40',
    nav: 'border-cyan-500 text-cyan-700 dark:text-cyan-300',
    tabActive: 'bg-white text-cyan-700 shadow-sm dark:bg-zinc-900 dark:text-cyan-300',
  },
}

export function communityClasses(color: string) {
  return COMMUNITY_CLASSES[color as GameCommunity['color']] ?? COMMUNITY_CLASSES.blue
}

/** 各分区可用的平台筛选（/api/contents 的 platform 参数值）。 */
export const PLATFORMS_BY_COMMUNITY: Record<GameCommunityKey, string[]> = {
  wuwa: ['bilibili', 'youtube', 'kuro'],
  blhx: ['bilibili'],
}

export function platformsForCommunity(community: string): string[] {
  return PLATFORMS_BY_COMMUNITY[community as GameCommunityKey] ?? ['bilibili']
}

export function useGameSection() {
  const route = useRoute()
  const router = useRouter()
  const stored = useState<GameCommunityKey>('game-section-stored', () => DEFAULT_COMMUNITY)
  const hydrated = useState('game-section-hydrated', () => false)

  const community = computed<GameCommunityKey>(() => {
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
    await router.push({ path: '/', query: { community: key } })
  }

  if (import.meta.client) onMounted(hydrate)

  return { community, currentCommunity, communityQuery, setCommunity, selectCommunity, communities: GAME_COMMUNITIES }
}
