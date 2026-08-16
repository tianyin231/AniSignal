<script setup lang="ts">
import { products, events } from '~/utils/data'

const route = useRoute()
const router = useRouter()
const product = products.find((p) => p.slug === route.params.slug)
if (!product) throw createError({ statusCode: 404, statusMessage: '商品不存在', fatal: true })

const tabs = [
  { key: 'overview', label: '总览' },
  { key: 'timeline', label: '履约时间线' },
  { key: 'unbox', label: '开箱与评测' },
  { key: 'quality', label: '质量与售后' },
  { key: 'evidence', label: '证据库' },
]
const tab = computed({
  get: () => String(route.query.tab || 'overview'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})

const productEvents = [...events]
  .filter((e) => ['延期', '尾款', '到货', '开箱', '质量', '售后', '预售'].includes(e.category))
  .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
const unboxEvents = productEvents.filter((e) => ['开箱', '评测'].includes(e.category))
const qualityEvents = productEvents.filter((e) => ['质量', '售后'].includes(e.category))
const allSources = productEvents.flatMap((e) => e.sources)

const facts = [
  { k: '预售开启', v: product.presaleAt },
  { k: '尾款窗口', v: `${product.balanceFrom} ~ ${product.balanceTo}` },
  { k: '原定发货', v: product.originalShipAt },
  { k: '当前口径', v: product.currentShipNote },
  { k: 'SKU', v: product.skus.join(' / ') },
  { k: '价格', v: product.price },
]

useSeoMeta({ title: product.name })
</script>

<template>
  <div>
    <BackButton fallback="/products" class="mb-3" />
    <section class="panel mb-4 p-4 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">{{ product.name }}</h1>
          <p class="mt-0.5 text-xs text-slate-400 dark:text-zinc-500">{{ product.publisher }}</p>
        </div>
        <div class="flex items-center gap-2">
          <span class="chip bg-blue-500/10 font-medium text-blue-600 dark:text-blue-300">
            {{ product.stages[product.currentStage].label }}
          </span>
          <span class="mono text-xs text-slate-400">{{ product.price }}</span>
        </div>
      </div>
    </section>

    <TabNav v-model="tab" :tabs="tabs" />

    <div v-if="tab === 'overview'" class="grid gap-4 lg:grid-cols-2">
      <section class="panel p-4">
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">履约状态</h2>
        <p class="mt-0.5 text-xs text-slate-400 dark:text-zinc-500">
          送测样品与普通用户订单严格区分，转载不重复计数。
        </p>
        <div class="mt-4">
          <FulfillmentStepper :stages="product.stages" variant="full" />
        </div>
      </section>
      <div class="space-y-4">
        <PurchaseTimeExplainer />
        <section class="panel p-4">
          <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">关键节点</h2>
          <dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
            <div v-for="f in facts" :key="f.k">
              <dt class="text-xs text-slate-400 dark:text-zinc-500">{{ f.k }}</dt>
              <dd class="mono mt-0.5 text-[13px] text-slate-700 dark:text-zinc-300">{{ f.v }}</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>

    <section v-else-if="tab === 'timeline'" class="panel overflow-hidden">
      <EventRow v-for="e in productEvents" :key="e.id" :event="e" />
    </section>

    <template v-else-if="tab === 'unbox'">
      <div class="mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-300">
        送测内容仅证明实物样品存在，不代表消费者订单开始发货。未声明来源的内容标记「来源未说明」，不做软广认定。
      </div>
      <section class="panel overflow-hidden">
        <EventRow v-for="e in unboxEvents" :key="e.id" :event="e" />
      </section>
    </template>

    <section v-else-if="tab === 'quality'" class="panel overflow-hidden">
      <EventRow v-for="e in qualityEvents" :key="e.id" :event="e" />
      <p class="border-t border-slate-200/80 px-4 py-3 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        出现重复性质量问题或重要使用风险时，商品监控订阅者会收到即时邮件。
      </p>
    </section>

    <section v-else class="panel p-4">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">全部证据（{{ allSources.length }}）</h2>
      <p class="mt-0.5 text-xs text-slate-400 dark:text-zinc-500">
        原始内容删除或修改后将记录状态，不冒充当前有效内容。
      </p>
      <div class="mt-3">
        <SourceTable :sources="allSources" />
      </div>
    </section>
  </div>
</template>
