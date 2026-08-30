<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import type { PublicContentList } from '~/composables/usePublicApi'
import { toRawItem } from '~/utils/public-utils'
import { platformMeta, type Platform } from '~/utils/data'

useSeoMeta({ title: '内容库 · AniSignal' })
const { t } = useSiteConfig()

const route = useRoute()
const router = useRouter()
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const platform = computed(() => String(route.query.platform || ''))
const q = computed(() => String(route.query.q || ''))
const searchInput = ref(q.value)
watch(q, (v) => (searchInput.value = v))
const pageSize = 30

const { api } = usePublicApi()
const { data, status } = await useAsyncData(
  () => `contents-${platform.value}-${q.value}-${page.value}`,
  () =>
    api<PublicContentList>('/api/contents', {
      query: {
        limit: pageSize,
        offset: (page.value - 1) * pageSize,
        ...(platform.value ? { platform: platform.value } : {}),
        ...(q.value ? { q: q.value } : {}),
      },
    }),
  { watch: [platform, q, page] },
)

const list = computed(() => (data.value?.items ?? []).map(toRawItem))
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))

function applyQuery(patch: Record<string, string | undefined>) {
  router.replace({ query: { ...route.query, page: undefined, ...patch } })
}
</script>

<template>
  <div>
    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <form class="flex items-center gap-2" @submit.prevent="applyQuery({ q: searchInput.trim() || undefined })">
        <div class="flex items-center gap-1.5 rounded-md border border-slate-900/10 bg-white/70 px-2.5 py-1.5 dark:border-white/15 dark:bg-white/5">
          <Search :size="13" class="text-slate-400" />
          <input v-model="searchInput" placeholder="标题关键词" class="w-36 bg-transparent text-xs outline-none sm:w-48" >
        </div>
        <button type="submit" class="btn-primary px-3 py-1.5 text-xs">搜索</button>
      </form>
    </div>

    <div class="mb-3 flex flex-wrap gap-1.5">
      <button
        class="rounded-full px-3 py-1 text-xs transition-colors"
        :class="!platform ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
        @click="applyQuery({ platform: undefined })"
      >
        全部平台
      </button>
      <button
        v-for="(meta, key) in platformMeta"
        :key="key"
        class="rounded-full px-3 py-1 text-xs transition-colors"
        :class="platform === key ? 'bg-blue-600 text-white' : 'bg-slate-500/10 text-slate-500 hover:text-slate-700 dark:text-zinc-400'"
        @click="applyQuery({ platform: key })"
      >
        {{ meta.label }}
      </button>
    </div>

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>
    <section v-else class="panel overflow-hidden">
      <RawFeedRow v-for="item in list" :key="item.id" :item="item" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">没有匹配的内容。</p>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ data?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="applyQuery({ page: String(page - 1) })">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="applyQuery({ page: String(page + 1) })">下一页 »</button>
        </div>
      </div>
    </section>
  </div>
</template>
