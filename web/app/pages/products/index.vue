<script setup lang="ts">
import type { ApiProductList } from '~/composables/usePublicApi'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'
import { sortProductsBySignal } from '~/utils/product-utils'

function communityChip(community?: string) {
  const meta = GAME_COMMUNITIES.find(c => c.key === community)
  return meta ? { label: meta.label, chip: communityClasses(meta.color).chip } : null
}

useSeoMeta({ title: '商品总览' })

const { api } = usePublicApi()
// community=all（首页聚合进入）时跨分区展示并逐卡标注分区
const { scope, isAll } = useCommunityScope()
const { data, status, error, refresh } = await useLazyAsyncData(
  () => `products-${scope.value}`,
  (_app, { signal }) => api<ApiProductList>('/api/products', { query: { community: scope.value }, signal }),
  { watch: [scope] },
)
const products = computed(() => sortProductsBySignal(data.value?.items ?? []))
useVisibleRefresh(() => refresh({ dedupe: 'defer' }), () => status.value !== 'pending')
</script>

<template>
  <div class="min-w-0">
    <SectionNav />
    <h1 class="text-2xl font-bold text-ink">商品总览</h1>
    <p class="mb-4 mt-1 text-sm text-slate-600 dark:text-zinc-400">{{ isAll ? '全部分区' : '当前分区' }}的商品履约动态，按最近信号更新排序。</p>

    <section v-if="status === 'pending' && !data" class="panel px-4 py-10 text-center text-sm text-slate-600 dark:text-zinc-400">
      加载中…
    </section>
    <section v-else-if="error" role="alert" class="panel px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">商品暂时加载失败。<button type="button" class="ml-1 underline" @click="refresh()">重新加载</button></section>
    <div v-else-if="products.length" class="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-4">
      <NuxtLink
        v-for="p in products"
        :key="p.slug"
        :to="{ path: `/products/${p.slug}`, query: { community: scope } }"
        class="panel min-w-0 p-4 transition-shadow hover:shadow-lg sm:p-5 [overflow-wrap:anywhere]"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="flex items-center gap-1.5 truncate font-semibold text-slate-900 dark:text-zinc-50">
              <span class="truncate">{{ p.name }}</span>
              <span
                v-if="isAll && communityChip(p.community)"
                class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
                :class="communityChip(p.community)!.chip"
              >{{ communityChip(p.community)!.label }}</span>
            </h2>
            <p class="mt-0.5 truncate text-xs text-slate-600 dark:text-zinc-400">
              {{ p.publisher || '—' }}{{ p.skus.length ? ` · ${p.skus.join(' / ')}` : '' }}
            </p>
          </div>
          <span v-if="p.monitor_status === 'archived'" class="chip shrink-0">窗口已结束</span>
        </div>
        <div v-if="p.brief" class="mt-3">
          <ProductBrief :brief="p.brief" />
        </div>
      </NuxtLink>
    </div>
    <section v-else class="panel px-4 py-10 text-center text-sm text-slate-600 dark:text-zinc-400">
      暂无商品动态。
    </section>

  </div>
</template>
