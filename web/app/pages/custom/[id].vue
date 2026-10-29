<script setup lang="ts">
import { fmtTime } from '~/utils/admin-types'
import { customKindLabel, customPlatformBadge } from '~/utils/custom-feeds'

useSeoMeta({ title: '定制监控 · AniSignal' })

interface CustomItem {
  id: number
  title: string | null
  url: string | null
  cover: string | null
  author_name: string | null
  view: number
  content_type: string
  platform: string
  published_at: string | null
}
interface CustomFeedDetail {
  id: number
  name: string
  kind: string
  platform: string
  target_value: string
  target_label: string
  description: string
  source_url: string | null
  min_views: number
  is_public: boolean
  owner_name: string | null
  item_count: number | null
  total: number
  enabled: boolean
  status: string
  is_owner: boolean
  items: CustomItem[]
}

const route = useRoute()
const router = useRouter()
const feedId = computed(() => Number(route.params.id))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageSize = 30
const { api } = usePublicApi()
const forwardedHeaders = useRequestHeaders(['cookie'])
const { error, data, status, refresh } = await useLazyAsyncData(
  () => `custom-feed-${feedId.value}-${page.value}`,
  async (_app, { signal }) => {
    try {
      return await api<CustomFeedDetail>(`/api/custom/feeds/${feedId.value}`, {
        headers: forwardedHeaders, signal, query: { page: page.value, page_size: pageSize },
      })
    } catch (exc: any) {
      if (exc?.status === 404) return null
      throw exc
    }
  },
)
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))
const goPage = (value: number) => router.replace({ query: { ...route.query, page: value > 1 ? value : undefined } })
</script>

<template>
  <div>
    <section v-if="status === 'pending'" class="panel px-4 py-10 text-center text-sm text-slate-400">加载中…</section>
    <section v-else-if="error || !data" class="panel px-4 py-10 text-center text-sm text-slate-500 dark:text-zinc-400">
      <p>{{ error ? '监控暂时加载失败，请稍后刷新。' : '监控不存在，或需要登录所属账号后查看。' }}</p>
      <button v-if="error" type="button" class="mt-3 underline" @click="refresh()">重新加载</button>
      <NuxtLink to="/account#custom-monitors" class="link mt-3 inline-block">我的监控</NuxtLink>
    </section>
    <template v-else>
      <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div class="min-w-0">
          <h1 class="truncate text-2xl font-bold text-ink">{{ data.name }}</h1>
          <p class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500">
            {{ customKindLabel(data.kind) }} · {{ data.target_label || data.target_value }}
            <template v-if="data.min_views > 0"> · 播放量 ≥ {{ data.min_views.toLocaleString('zh-CN') }}</template>
            · {{ data.owner_name || '匿名' }}
          </p>
        </div>
        <div class="flex items-center gap-3 text-xs">
          <span class="mono text-slate-400">{{ data.total }} 条</span>
          <a v-if="data.source_url" :href="data.source_url" target="_blank" rel="noopener" class="link">官方主页</a>
          <NuxtLink v-if="data.is_owner" to="/account#custom-monitors" class="link">管理监控</NuxtLink>
        </div>
      </div>
      <p v-if="data.description" class="mb-4 text-sm leading-relaxed text-muted">{{ data.description }}</p>

      <section class="panel overflow-hidden">
        <article
          v-for="item in data.items"
          :key="item.id"
          class="flex flex-wrap items-center gap-3 border-b border-slate-900/5 px-4 py-3 last:border-0 dark:border-white/5"
        >
          <span
            class="grid size-5 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white"
            :class="item.platform === 'youtube' ? 'bg-red-600' : item.platform === 'x' ? 'bg-slate-800 dark:bg-slate-100 dark:text-slate-900' : item.content_type === 'notice' ? 'bg-blue-600' : item.content_type === 'dynamic' ? 'bg-sky-500' : 'bg-pink-500'"
          >{{ customPlatformBadge(item.platform, item.content_type) }}</span>
          <div class="min-w-0 flex-1">
            <a
              v-if="item.url"
              :href="item.url"
              target="_blank"
              rel="noopener"
              class="line-clamp-2 text-[14px] text-slate-800 hover:text-blue-700 dark:text-zinc-200 dark:hover:text-blue-300"
            >{{ item.title || item.url }}</a>
            <span v-else class="block truncate text-[14px] text-slate-800 dark:text-zinc-200">{{ item.title }}</span>
            <p class="mt-0.5 truncate text-xs text-slate-400 dark:text-zinc-500">
              {{ item.author_name || '—' }}<template v-if="item.view"> · {{ item.view.toLocaleString('zh-CN') }} {{ item.content_type === 'notice' ? '阅读' : '播放' }}</template>
            </p>
          </div>
          <span class="mono ml-8 w-full shrink-0 text-xs text-slate-400 sm:ml-0 sm:w-auto">{{ fmtTime(item.published_at) }}</span>
        </article>
        <p v-if="!data.items.length" class="px-4 py-10 text-center text-sm text-slate-400">
          {{ data.total ? '这一页没有内容，请返回上一页。' : data.status !== 'active' ? '该监控已被管理员停用。' : data.enabled ? '还没有抓到内容，监控会在后台持续抓取。' : '监控已暂停，恢复后会继续抓取。' }}
        </p>
      </section>
      <nav v-if="totalPages > 1" aria-label="监控内容分页" class="mt-4 flex items-center justify-between px-1 text-xs text-slate-500 dark:text-zinc-400">
        <span>共 {{ data.total }} 条</span>
        <div class="flex items-center gap-3">
          <button :disabled="page <= 1" class="min-h-11 disabled:opacity-40" @click="goPage(page - 1)">上一页</button>
          <span>{{ page }} / {{ totalPages }}</span>
          <button :disabled="page >= totalPages" class="min-h-11 disabled:opacity-40" @click="goPage(page + 1)">下一页</button>
        </div>
      </nav>
    </template>
  </div>
</template>
