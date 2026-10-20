<script setup lang="ts">
import { Search } from 'lucide-vue-next'

const q = ref('')
const router = useRouter()
const route = useRoute()
const pageCommunity = useState<{ path: string, key: string } | null>('page-community', () => null)
const isAi = computed(() =>
  route.path === '/ai'
  || route.path.startsWith('/ai/')
  || route.query.community === 'ai'
  || (pageCommunity.value?.path === route.path && pageCommunity.value.key === 'ai'),
)
const searchPath = computed(() => isAi.value ? '/ai' : '/search')
const searchQuery = computed(() => isAi.value ? {} : { community: String(route.query.community || 'all') })

function go() {
  const s = q.value.trim()
  if (s) router.push({ path: searchPath.value, query: { ...searchQuery.value, q: s } })
}
</script>

<template>
  <form class="relative mr-1 hidden md:block" role="search" @submit.prevent="go">
    <Search :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
    <input
      v-model="q"
      type="search"
      aria-label="站内搜索"
      :placeholder="isAi ? '搜索 AI 快讯' : '搜索事件、角色、活动'"
      class="input h-10 w-48 bg-canvas pl-9 pr-3 text-[13px] lg:w-60"
      @keydown.enter.prevent="go"
    />
  </form>
  <NuxtLink
    :to="{ path: searchPath, query: searchQuery }"
    aria-label="搜索"
    class="grid size-10 place-items-center rounded-[10px] text-muted transition-colors hover:bg-chip hover:text-ink md:hidden"
  >
    <Search :size="18" />
  </NuxtLink>
</template>
