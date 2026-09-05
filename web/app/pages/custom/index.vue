<script setup lang="ts">
useSeoMeta({ title: '定制化 · AniSignal' })

interface CustomFeedCard {
  id: number
  name: string
  kind: string
  platform: string
  target_value: string
  item_count: number
  owner_name: string | null
  created_at: string
}

const { api } = usePublicApi()
const page = computed(() => Math.max(1, Number(useRoute().query.page) || 1))
const pageSize = 30

const { data, status } = await useAsyncData(
  () => `custom-feeds-${page.value}`,
  () =>
    api<{ items: CustomFeedCard[], total: number }>('/api/custom/feeds', {
      query: { page: page.value, page_size: pageSize },
    }),
  { watch: [page] },
)

const items = computed(() => data.value?.items ?? [])
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))
const router = useRouter()
function goPage(p: number) {
  router.replace({ query: { ...useRoute().query, page: p > 1 ? String(p) : undefined } })
}

const kindLabel = (kind: string) => (kind === 'uid' ? 'UID 监控' : '关键词监控')
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between gap-2">
      <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">定制化</h1>
      <NuxtLink to="/account" class="link shrink-0 text-xs">创建我的监控 →</NuxtLink>
    </div>

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <template v-else>
      <div v-if="items.length" class="grid gap-3 sm:grid-cols-2">
        <NuxtLink
          v-for="feed in items"
          :key="feed.id"
          :to="`/custom/${feed.id}`"
          class="panel block p-4 transition-shadow hover:shadow-lg"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="chip whitespace-nowrap bg-blue-500/10 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300">
              {{ kindLabel(feed.kind) }}
            </span>
            <span class="mono text-[11px] text-slate-400">{{ feed.item_count }} 条</span>
          </div>
          <h2 class="mt-2 truncate font-semibold text-slate-900 dark:text-zinc-50">{{ feed.name }}</h2>
          <p class="mt-1 truncate text-xs text-slate-500 dark:text-zinc-400">监控目标：{{ feed.target_value }}</p>
          <p class="mt-1 truncate text-xs text-slate-400 dark:text-zinc-500">
            {{ feed.owner_name || '匿名' }} · 持续抓取中
          </p>
        </NuxtLink>
      </div>
      <div v-else class="panel px-4 py-10 text-center text-sm text-slate-400">
        还没有公开的定制监控。在账号中心创建并选择"公开到全站"后会出现在这里。
      </div>

      <div v-if="totalPages > 1" class="mt-4 flex items-center justify-between px-1 text-xs text-slate-400">
        <span>共 {{ data?.total }} 个公开监控</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </template>
  </div>
</template>
