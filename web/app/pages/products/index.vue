<script setup lang="ts">
import type { ApiProductList } from '~/composables/usePublicApi'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'

function communityChip(community?: string) {
  const meta = GAME_COMMUNITIES.find(c => c.key === community)
  return meta ? { label: meta.label, chip: communityClasses(meta.color).chip } : null
}

useSeoMeta({ title: '商品' })

const { api } = usePublicApi()
// community=all（首页聚合进入）时跨分区展示并逐卡标注分区
const { scope, isAll } = useCommunityScope()
const { data, status, error, refresh } = await useLazyAsyncData(
  () => `products-${scope.value}`,
  (_app, { signal }) => api<ApiProductList>('/api/products', { query: { community: scope.value }, signal }),
  { watch: [scope] },
)
const products = computed(() => data.value?.items ?? [])
</script>

<template>
  <div>
    <BackToChannelHome />
    <h1 class="mb-4 text-lg font-semibold text-slate-900 dark:text-zinc-50">商品</h1>

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <section v-else-if="error" role="alert" class="panel px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">商品暂时加载失败。<button type="button" class="ml-1 underline" @click="refresh()">重新加载</button></section>
    <div v-else-if="products.length" class="grid gap-4 sm:grid-cols-2">
      <NuxtLink
        v-for="p in products"
        :key="p.slug"
        :to="`/products/${p.slug}`"
        class="panel block p-4 transition-shadow hover:shadow-lg sm:p-5"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="flex items-center gap-1.5 truncate font-semibold text-slate-900 dark:text-zinc-50">
              <span class="truncate">{{ p.name }}</span>
              <span
                v-if="isAll && communityChip(p.community)"
                class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
                :class="communityChip(p.community)!.chip"
              >{{ communityChip(p.community)!.label }}</span>
            </h2>
            <p class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500">
              {{ p.publisher || '—' }}{{ p.skus.length ? ` · ${p.skus.join(' / ')}` : '' }}
            </p>
          </div>
          <span class="chip shrink-0 bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ p.fulfillment.consumer.state_label }}</span>
        </div>
        <p v-if="p.price_note" class="mono mt-3 text-sm text-slate-600 dark:text-zinc-300">{{ p.price_note }}</p>
        <div class="mt-3 grid gap-2 text-xs text-slate-500 dark:text-zinc-400">
          <p><span class="text-slate-400">官方：</span>{{ p.fulfillment.official.state_label }}</p>
          <p><span class="text-slate-400">消费者：</span>{{ p.fulfillment.consumer.state_label }}</p>
          <p><span class="text-slate-400">质量：</span>{{ p.fulfillment.quality.state_label }}</p>
        </div>
        <p v-if="p.latest_candidate" class="mt-3 truncate text-[11px] text-slate-500 dark:text-zinc-400" :title="p.latest_candidate.summary">
          最新候选：{{ p.latest_candidate.summary }}（未核验）
        </p>
        <p v-if="p.fulfillment.candidate_total" class="mt-3 text-[11px] text-amber-600 dark:text-amber-300">{{ p.fulfillment.candidate_total }} 条候选信号待核验</p>
      </NuxtLink>
    </div>
    <section v-else class="panel px-4 py-10 text-center text-sm text-slate-400">
      暂无追踪中的商品。商品与监控词由管理员在后台维护。
    </section>

  </div>
</template>
