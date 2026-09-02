/**
 * 游戏分区（鸣潮 / 碧蓝航线）：公开站大标签页切换的单一状态源。
 *
 * 规则：
 * - URL query `?community=` 优先（分享链接与刷新后保持分区一致；默认分区不写进 URL 保持干净）；
 * - 无 query 时回退到 localStorage 记忆（客户端挂载后生效，避免 SSR 水合不一致）；
 * - 切换分区时重置页码，并把记忆写入 localStorage。
 * 分区清单与后端 site_config.communities / COMMUNITY_LABELS 保持一致。
 */

export interface GameCommunity {
  key: 'wuwa' | 'blhx'
  label: string
}

export const GAME_COMMUNITIES: GameCommunity[] = [
  { key: 'wuwa', label: '鸣潮' },
  { key: 'blhx', label: '碧蓝航线' },
]

export type GameCommunityKey = GameCommunity['key']
const DEFAULT_COMMUNITY: GameCommunityKey = 'wuwa'
const STORAGE_KEY = 'anisignal-community'
const VALID_KEYS = new Set(GAME_COMMUNITIES.map(item => item.key))

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

  if (import.meta.client) onMounted(hydrate)

  return { community, communityQuery, setCommunity, communities: GAME_COMMUNITIES }
}
