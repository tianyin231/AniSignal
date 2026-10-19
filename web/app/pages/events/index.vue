<script setup lang="ts">
import type { ApiEventList } from '~/composables/usePublicApi'
import { categoryMeta, toAppEvent } from '~/utils/public-utils'
import type { AppEvent } from '~/utils/data'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'official_announce', label: '公告' },
  { key: 'delay', label: '延期' },
  { key: 'arrived', label: '到货' },
  { key: 'unboxing', label: '开箱' },
  { key: 'quality', label: '质量' },
  { key: 'version_update', label: '版本' },
]
const route = useRoute()
const router = useRouter()
const tab = computed({
  get: () => String(route.query.tab || 'all'),
  set: (v: string) => router.replace({ query: { ...route.query, tab: v } }),
})
const requestedPage = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30
// PERF-039：服务端 offset 上界 10_000（见 app/api/public.py）。只钳下界时
// `/events?page=99999` 会真的发出 offset=2,999,940——InnoDB 必须读取并丢弃前 300 万行，
// 而这是无需认证的公开端点。这里按 totalPages 钳上界（正确范式：同仓库 account.vue:180）。
const total = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const page = computed(() => Math.min(requestedPage.value, totalPages.value))

const { api } = usePublicApi()
// community=all（首页聚合进入）时跨分区展示并逐行标注分区
const { scope, isAll } = useCommunityScope()
const { data, status } = await useAsyncData(
  () => `events-${scope.value}-${tab.value}-${page.value}`,
  () =>
    api<ApiEventList>('/api/events', {
      query: {
        community: scope.value,
        limit: pageSize,
        offset: (page.value - 1) * pageSize,
        ...(tab.value !== 'all' ? { category: tab.value } : {}),
      },
    }),
  { watch: [scope, tab, page] },
)

const list = computed<AppEvent[]>(() => (data.value?.items ?? []).map(toAppEvent))
// total 用独立 ref 而不是 computed：data 依赖 page、page 又依赖 totalPages，
// 直接写成 computed 会被 Vue 判成循环计算。watch 是副作用，不成环。
watch(data, (d) => (total.value = d?.total ?? 0), { immediate: true })

function goPage(p: number) {
  router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined } })
}

useSeoMeta({ title: '事件库' })
</script>

<template>
  <div>
    <BackToChannelHome />
    <TabNav v-model="tab" :tabs="tabs" />

    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">
      加载中…
    </section>
    <section v-else class="panel overflow-hidden">
      <EventRow v-for="e in list" :key="e.id" :event="e" :show-community="isAll" />
      <p v-if="!list.length" class="px-4 py-10 text-center text-sm text-slate-400">
        在已接入来源中暂无此类事件。
      </p>
      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-slate-200/80 px-4 py-2.5 text-xs text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
        <span>共 {{ data?.total ?? 0 }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page - 1)">« 上一页</button>
          <span class="mono">{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="disabled:opacity-40 hover:text-slate-600 dark:hover:text-zinc-300" @click="goPage(page + 1)">下一页 »</button>
        </div>
      </div>
    </section>
  </div>
</template>
