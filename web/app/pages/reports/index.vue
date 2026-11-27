<script setup lang="ts">
import { ArrowRight, FileText, Sparkles } from 'lucide-vue-next'
import { REPORT_PERIOD_LABELS } from '~/utils/lexicon'
import { relativeTime, toTimestamp } from '~/utils/schedule'

interface ReportSummary {
  id: number
  scope_type: 'community' | 'custom_bundle'
  scope_key: string
  scope_label: string
  period: 'daily' | 'weekly'
  period_key: string
  title: string
  overview: string
  item_count: number
  ai_generated: boolean
  created_at: string
}

interface CommunityPill {
  key: string
  label: string
  domain: string
  color: string
}

interface ReportsListResp {
  items: ReportSummary[]
  total: number
  communities: CommunityPill[]
}

useSeoMeta({
  title: '周期报告 · AniSignal',
  description: '按日或按周汇总各分区与个人定制监控的重点进展，支持站内精读与定时推送。',
})

const route = useRoute()
const router = useRouter()
const { api } = usePublicApi()
const { user } = useAuth()

const mine = computed(() => route.query.mine === '1' && !!user.value)
const period = computed(() => {
  const p = String(route.query.period ?? '')
  return p === 'daily' || p === 'weekly' ? p : ''
})
const scopeKey = computed(() => (mine.value ? '' : String(route.query.community ?? '')))
const now = ref(Date.now())

const { data, pending, error, refresh } = await useAsyncData(
  () => `periodic-reports-${mine.value ? 'mine' : 'pub'}-${scopeKey.value}-${period.value}`,
  (_app, { signal }) => api<ReportsListResp>('/api/reports', {
    query: {
      scope_key: scopeKey.value || undefined,
      period: period.value || undefined,
      mine: mine.value || undefined,
      limit: 30,
    },
    signal,
  }),
  { watch: [mine, period, scopeKey] },
)

const items = computed(() => data.value?.items ?? [])
const communities = computed(() => data.value?.communities ?? [])

function setQuery(patch: Record<string, string | undefined>) {
  const next: Record<string, string> = {}
  const merged = {
    mine: mine.value ? '1' : undefined,
    period: period.value || undefined,
    community: scopeKey.value || undefined,
    ...patch,
  }
  for (const [k, v] of Object.entries(merged)) {
    if (v) next[k] = v
  }
  router.replace({ path: '/reports', query: next })
}
</script>

<template>
  <div class="space-y-5">
    <section class="flex flex-wrap items-end justify-between gap-4">
      <div class="wall-heading">
        <p class="mono mb-1 text-xs tracking-wide text-muted">PERIODIC REPORTS</p>
        <h1 class="text-[26px] font-bold leading-tight text-ink sm:text-[30px]">周期报告</h1>
        <p class="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted">按日或按周归档分区与定制监控的重点动态，支持邮件与 Webhook 定时送达。</p>
      </div>
      <NuxtLink
        to="/account?tab=notify#periodic-reports"
        class="btn-primary inline-flex h-10 shrink-0 items-center gap-1.5 px-4 text-sm"
      >
        配置定时推送 <ArrowRight :size="15" aria-hidden="true" />
      </NuxtLink>
    </section>

    <div class="wall-surface flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-card/85 p-3">
      <div v-if="user" class="inline-flex rounded-xl bg-chip p-0.5 text-xs font-medium">
        <button
          type="button"
          class="h-8 rounded-[10px] px-3 transition-colors"
          :class="!mine ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
          @click="setQuery({ mine: undefined })"
        >
          公开分区
        </button>
        <button
          type="button"
          class="h-8 rounded-[10px] px-3 transition-colors"
          :class="mine ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
          @click="setQuery({ mine: '1', community: undefined })"
        >
          我的定制报告
        </button>
      </div>

      <div class="inline-flex rounded-xl bg-chip p-0.5 text-xs font-medium">
        <button
          v-for="opt in [{ key: '', label: '全部周期' }, { key: 'daily', label: '日报' }, { key: 'weekly', label: '周报' }]"
          :key="opt.key"
          type="button"
          class="h-8 rounded-[10px] px-3 transition-colors"
          :class="period === opt.key ? 'bg-card font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'"
          @click="setQuery({ period: opt.key || undefined })"
        >
          {{ opt.label }}
        </button>
      </div>

      <div v-if="!mine && communities.length" class="flex flex-wrap items-center gap-1.5 pl-1">
        <button
          type="button"
          class="h-7 rounded-full px-2.5 text-xs transition-colors"
          :class="!scopeKey ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
          @click="setQuery({ community: undefined })"
        >
          全部分区
        </button>
        <button
          v-for="comm in communities"
          :key="comm.key"
          type="button"
          class="h-7 rounded-full px-2.5 text-xs transition-colors"
          :class="scopeKey === comm.key ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
          @click="setQuery({ community: comm.key })"
        >
          {{ comm.label }}
        </button>
      </div>
    </div>

    <UiNotice v-if="error" tone="warn">
      报告列表加载失败。<button type="button" class="ml-2 font-semibold underline" @click="refresh()">重试</button>
    </UiNotice>

    <div v-else-if="pending && !items.length" class="panel p-8 text-center text-sm text-faint">
      正在加载周期报告…
    </div>

    <div v-else-if="items.length" class="grid gap-4 md:grid-cols-2">
      <NuxtLink
        v-for="row in items"
        :key="row.id"
        :to="`/reports/${row.id}`"
        class="panel group flex flex-col p-5 transition-colors hover:border-brand/50"
      >
        <div class="flex flex-wrap items-center gap-1.5 text-xs">
          <span class="rounded bg-brand-soft px-2 py-0.5 font-semibold text-brand-ink">
            {{ REPORT_PERIOD_LABELS[row.period] ?? row.period }}
          </span>
          <span class="rounded bg-chip px-2 py-0.5 text-ink-2">{{ row.scope_label }}</span>
          <span v-if="row.ai_generated" class="inline-flex items-center gap-1 rounded bg-chip px-2 py-0.5 text-brand-ink">
            <Sparkles :size="11" aria-hidden="true" /> AI 聚类
          </span>
          <span class="mono ml-auto tabular-nums text-faint">{{ row.period_key }}</span>
        </div>

        <h2 class="mt-3 text-base font-bold leading-snug text-ink group-hover:text-brand-ink">
          {{ row.title }}
        </h2>
        <p v-if="row.overview" class="mt-2 line-clamp-2 text-sm leading-relaxed text-muted">
          {{ row.overview }}
        </p>

        <div class="mt-auto flex items-center justify-between pt-4 text-xs text-faint">
          <span class="tabular-nums">共 {{ row.item_count }} 条</span>
          <span>{{ relativeTime(toTimestamp(row.created_at), now) }}</span>
        </div>
      </NuxtLink>
    </div>

    <div v-else class="panel flex flex-col items-center justify-center px-6 py-12 text-center">
      <span class="grid size-11 place-items-center rounded-2xl bg-chip text-muted" aria-hidden="true">
        <FileText :size="20" />
      </span>
      <p class="mt-3 text-sm font-semibold text-ink">暂无已生成的周期报告</p>
      <p class="mt-1 max-w-md text-xs leading-relaxed text-muted">
        在账号中心开启任意分区或定制监控的日报、周报订阅后，系统会在北京时间 08:30 自动归档并推送，也可随时手动触发生成。
      </p>
      <NuxtLink to="/account?tab=notify#periodic-reports" class="btn-primary mt-4 inline-flex h-9 items-center px-4 text-xs">
        前往配置订阅
      </NuxtLink>
    </div>
  </div>
</template>
