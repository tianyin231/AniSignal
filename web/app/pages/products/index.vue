<script setup lang="ts">
import type { ApiProductList } from '~/composables/usePublicApi'
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'
import { sortProductsBySignal } from '~/utils/product-utils'
import { fmtTime } from '~/utils/admin-types'

function communityChip(community?: string) {
  const meta = GAME_COMMUNITIES.find(c => c.key === community)
  return meta ? { label: meta.label, chip: communityClasses(meta.color).chip } : null
}

/** 列表卡只留一行履约摘要；三块明细进详情页看。 */
function briefLine(product: ApiProductList['items'][number]) {
  const brief = product.brief
  if (!brief) return ''
  const parts: string[] = []
  if (brief.unboxing_accounts) parts.push(`${brief.unboxing_accounts} 个账号开箱`)
  else if (brief.unboxing_note) parts.push(brief.unboxing_note)
  if (brief.quality_accounts) parts.push(`${brief.quality_accounts} 条质量反馈`)
  if (!parts.length && product.monitor_status !== 'archived') parts.push(product.fulfillment.official.state_label || '持续追踪官方动态')
  return parts.join(' · ')
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
const filter = ref('all')
const filters = [{ value: 'all', label: '全部' }, { value: 'watching', label: '追踪中' }, { value: 'archived', label: '已归档' }]
const visibleProducts = computed(() => products.value.filter(product => filter.value === 'all' || (product.monitor_status || 'watching') === filter.value))
useVisibleRefresh(() => refresh({ dedupe: 'defer' }), () => status.value !== 'pending')
</script>

<template>
  <div class="min-w-0">
    <SectionNav />
    <div class="mb-6 mt-2 flex flex-wrap items-end justify-between gap-4">
      <h1 class="text-3xl font-bold tracking-tight text-ink">商品追踪<span v-if="products.length" class="num ml-2 text-lg font-medium text-muted">{{ products.length }}</span></h1>
      <div class="flex gap-1 rounded-xl border border-line bg-card p-1" aria-label="商品状态筛选">
        <button v-for="item in filters" :key="item.value" type="button" class="min-h-11 cursor-pointer rounded-lg px-3 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-brand" :aria-pressed="filter === item.value" :class="filter === item.value ? 'bg-brand-soft font-semibold text-brand-ink' : 'text-muted hover:bg-chip'" @click="filter = item.value">{{ item.label }}</button>
      </div>
    </div>

    <section v-if="status === 'pending' && !data" class="panel px-4 py-10 text-center text-sm text-slate-600 dark:text-zinc-400">
      加载中…
    </section>
    <section v-else-if="error" role="alert" class="panel px-4 py-10 text-center text-sm text-amber-700 dark:text-amber-300">商品暂时加载失败。<button type="button" class="ml-1 underline" @click="refresh()">重新加载</button></section>
    <div v-else-if="visibleProducts.length" class="grid auto-rows-fr grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      <NuxtLink
        v-for="p in visibleProducts"
        :key="p.slug"
        :to="{ path: `/products/${p.slug}`, query: { community: scope } }"
        class="panel group flex min-w-0 flex-col overflow-hidden transition-shadow hover:shadow-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand [overflow-wrap:anywhere]"
      >
        <ProductArtwork :src="p.images?.[0]" :name="p.name" :kind="p.product_type" cover class="aspect-[16/10] shrink-0 border-b border-line-soft" />
        <div class="flex flex-1 flex-col p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
              <span v-if="p.product_type">{{ p.product_type }}</span>
              <span
                v-if="isAll && communityChip(p.community)"
                class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
                :class="communityChip(p.community)!.chip"
              >{{ communityChip(p.community)!.label }}</span>
              <span class="ml-auto inline-flex items-center gap-1.5"><span class="size-1.5 rounded-full" :class="p.monitor_status === 'archived' ? 'bg-faint' : 'bg-ok'" />{{ p.monitor_status === 'archived' ? '已归档' : '追踪中' }}</span>
            </div>
            <h2 class="line-clamp-2 min-h-12 text-lg font-bold leading-6 text-ink group-hover:text-brand-ink">
              {{ p.name }}
            </h2>
            <p class="num mt-2 line-clamp-2 min-h-12 text-base font-semibold leading-6 text-ink-2" :title="p.price_note || undefined">{{ p.price_note }}</p>
            <p class="mt-1 min-h-4 truncate text-xs text-muted">
              {{ [p.publisher, p.skus.join(' / ')].filter(Boolean).join(' · ') }}
            </p>
          </div>
        </div>
        <p class="mt-2 min-h-4 truncate text-xs text-muted">{{ briefLine(p) }}</p>
        <div class="mt-auto flex items-center justify-between gap-2 border-t border-line-soft pt-2.5 text-xs"><span class="mono text-faint">{{ p.fulfillment.updated_at ? fmtTime(p.fulfillment.updated_at) : '' }}</span><span class="shrink-0 font-medium text-brand-ink">查看进展 →</span></div>
        </div>
      </NuxtLink>
    </div>
    <section v-else class="panel px-4 py-10 text-center text-sm text-slate-600 dark:text-zinc-400">
      {{ products.length ? '该状态下暂无商品。' : '暂无商品动态。' }}
    </section>

  </div>
</template>
