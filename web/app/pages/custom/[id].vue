<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'

useSeoMeta({ title: '定制监控 · AniSignal' })

interface CustomItem {
  id: number
  title: string | null
  url: string | null
  cover: string | null
  author_name: string | null
  view: number
  content_type: string
  published_at: string | null
}
interface CustomFeedDetail {
  id: number
  name: string
  kind: string
  platform: string
  target_value: string
  min_views: number
  is_public: boolean
  owner_name: string | null
  item_count: number | null
  is_owner: boolean
  items: CustomItem[]
}

const route = useRoute()
const feedId = computed(() => Number(route.params.id))
const { api } = usePublicApi()
const forwardedHeaders = useRequestHeaders(['cookie'])
const { error, data } = await useAsyncData(
  () => `custom-feed-${feedId.value}`,
  async () => {
    try {
      return await api<CustomFeedDetail>(`/api/custom/feeds/${feedId.value}`, { headers: forwardedHeaders })
    } catch (exc: any) {
      if (exc?.status === 404) return null
      throw exc
    }
  },
  { watch: [feedId] },
)
</script>

<template>
  <div>
    <section v-if="!data" class="panel px-4 py-10 text-center text-sm text-slate-500 dark:text-zinc-400">
      <p>{{ error ? '监控暂时加载失败，请稍后刷新。' : '监控不存在，或需要登录所属账号后查看。' }}</p>
      <NuxtLink to="/account#custom-monitors" class="link mt-3 inline-block">我的监控</NuxtLink>
    </section>
    <template v-else>
      <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div class="min-w-0">
          <h1 class="truncate text-lg font-semibold text-slate-900 dark:text-zinc-50">{{ data.name }}</h1>
          <p class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500">
            {{ data.kind === 'uid' ? 'UID 监控' : '关键词监控' }} · {{ data.target_value }}
            <template v-if="data.min_views > 0"> · 播放量 ≥ {{ data.min_views.toLocaleString('zh-CN') }}</template>
            · {{ data.owner_name || '匿名' }}
          </p>
        </div>
        <span class="mono shrink-0 text-xs text-slate-400">{{ data.item_count ?? data.items.length }} 条</span>
      </div>

      <section class="panel overflow-hidden">
        <article
          v-for="item in data.items"
          :key="item.id"
          class="flex items-center gap-3 border-b border-slate-900/5 px-4 py-3 last:border-0 dark:border-white/5"
        >
          <span
            class="grid size-5 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white"
            :class="item.content_type === 'dynamic' ? 'bg-sky-500' : 'bg-pink-500'"
          >{{ item.content_type === 'dynamic' ? '动' : 'B' }}</span>
          <div class="min-w-0 flex-1">
            <a
              v-if="item.url"
              :href="item.url"
              target="_blank"
              rel="noopener"
              class="block truncate text-[14px] text-slate-800 hover:text-blue-700 dark:text-zinc-200 dark:hover:text-blue-300"
            >{{ item.title || item.url }}</a>
            <span v-else class="block truncate text-[14px] text-slate-800 dark:text-zinc-200">{{ item.title }}</span>
            <p class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500">
              {{ item.author_name || '—' }}<template v-if="item.view"> · {{ item.view.toLocaleString('zh-CN') }} 播放</template>
            </p>
          </div>
          <span class="mono shrink-0 text-xs text-slate-400">{{ fmtTime(item.published_at) }}</span>
        </article>
        <p v-if="!data.items.length" class="px-4 py-10 text-center text-sm text-slate-400">
          还没有抓到内容，监控会在后台持续抓取。
        </p>
      </section>
    </template>
  </div>
</template>
