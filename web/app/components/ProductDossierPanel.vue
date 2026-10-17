<script setup lang="ts">
/**
 * 商品档案面板（批次 3b）：档案头 + 价格/热度曲线 + AI 质量反馈 + 官方公告时间线。
 * 数据全部来自只读公开接口 /api/products/{slug}/dossier，价格与热度是「观测值」而非官方定价。
 */
import type { ApiProductDossier } from '~/composables/usePublicApi'
import { ExternalLink, Package, PackageCheck, Tag } from 'lucide-vue-next'

const props = defineProps<{ slug: string }>()
const { api } = usePublicApi()

const { data, pending, error } = await useAsyncData(
  () => `dossier-${props.slug}`,
  () => api<ApiProductDossier>(`/api/products/${props.slug}/dossier`, { query: { days: 90 } }),
  { watch: () => props.slug },
)

const dossier = computed<ApiProductDossier | null>(() => data.value ?? null)
const profile = computed(() => dossier.value?.profile ?? null)
const tracking = ref(false)
const trackHint = ref('')

// 加入跟踪 = 写一条默认「关注中」的状态（账号中心接口，未登录时引导去登录）
async function joinTracker() {
  if (!dossier.value)
    return
  tracking.value = true
  trackHint.value = ''
  try {
    await $fetch(`/api/account/product-tracker/${dossier.value.product_id}/state`, {
      method: 'PUT',
      body: { state: 'watching' },
    })
    trackHint.value = '已加入跟踪，可在账号中心标记状态与心理价位。'
  } catch (exc: any) {
    if (exc?.response?.status === 401 || exc?.status === 401)
      navigateTo(`/login?redirect=${encodeURIComponent(`/products/${props.slug}?view=dossier`)}`)
    else
      trackHint.value = exc?.data?.detail ?? exc?.message ?? '加入跟踪失败，请稍后重试。'
  } finally {
    tracking.value = false
  }
}

const releaseStateTone: Record<string, string> = {
  announced: 'bg-blue-500/10 text-blue-700 dark:text-blue-300',
  presale: 'bg-violet-500/10 text-violet-700 dark:text-violet-300',
  on_sale: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  delayed: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  shipped: 'bg-slate-500/10 text-slate-600 dark:text-zinc-300',
  unknown: 'bg-slate-500/10 text-slate-500 dark:text-zinc-400',
}

const sentimentTone: Record<string, string> = {
  negative: 'bg-rose-500/10 text-rose-700 dark:text-rose-300',
  mixed: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  positive: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
}

const series = computed(() => dossier.value?.series ?? [])
const points = computed(() =>
  series.value.filter((item) => item.price !== null).map((item) => item.price as number),
)
const priceRange = computed(() => {
  if (!points.value.length)
    return { min: 0, max: 0 }
  return { min: Math.min(...points.value), max: Math.max(...points.value) }
})
const maxHeat = computed(() =>
  Math.max(1, ...series.value.map((item) => item.heat ?? 0)),
)

function priceBar(price: number | null) {
  if (price === null || priceRange.value.max <= priceRange.value.min)
    return 4
  const ratio = (price - priceRange.value.min) / (priceRange.value.max - priceRange.value.min)
  return 6 + ratio * 78
}

function heatBar(heat: number | null) {
  if (heat === null)
    return 0
  return Math.round((heat / maxHeat.value) * 100)
}

function fmtDate(value: string) {
  return value.slice(5).replace('-', '/')
}

function fmtTime(value: string | null) {
  if (!value)
    return '—'
  return value.slice(0, 16).replace('T', ' ')
}

function fmtPrice(value: number | null) {
  return value === null ? '—' : `¥${value}`
}
</script>

<template>
  <div class="space-y-4">
    <div v-if="pending" class="panel p-6 text-center text-sm text-slate-400">档案加载中…</div>
    <div v-else-if="error" class="panel p-6 text-center text-sm text-rose-500">档案加载失败，请稍后重试。</div>
    <template v-else-if="dossier">
      <!-- 档案头 -->
      <section class="panel p-4 sm:p-5">
        <div class="flex flex-wrap items-start gap-4">
          <img
            v-if="profile?.cover_url"
            :src="profile.cover_url"
            :alt="dossier.name"
            class="h-28 w-28 shrink-0 rounded-lg object-cover"
            loading="lazy"
          >
          <div v-else class="flex h-28 w-28 shrink-0 items-center justify-center rounded-lg bg-slate-200/60 text-slate-400 dark:bg-zinc-800/70">
            <Package class="h-8 w-8" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="text-base font-semibold text-slate-900 dark:text-zinc-50">
                {{ profile?.official_name || dossier.name }}
              </h2>
              <span
                v-if="profile"
                class="chip"
                :class="releaseStateTone[profile.release_state] ?? releaseStateTone.unknown"
              >
                {{ profile.release_state_label }}
              </span>
              <button
                type="button"
                class="btn-primary ml-auto inline-flex items-center gap-1 px-3 py-1.5 text-xs"
                :disabled="tracking"
                @click="joinTracker"
              >
                <PackageCheck class="h-3.5 w-3.5" /> {{ tracking ? '提交中…' : '加入跟踪' }}
              </button>
            </div>
            <p v-if="trackHint" class="mt-1 text-xs text-emerald-600 dark:text-emerald-400">{{ trackHint }}</p>
            <p v-if="profile?.release_at" class="mono mt-1 text-xs text-slate-500 dark:text-zinc-400">
              发售时间 {{ fmtTime(profile.release_at) }}
            </p>
            <p v-if="profile?.release_note" class="mt-1 text-xs text-slate-500 dark:text-zinc-400">
              {{ profile.release_note }}
            </p>
            <p v-if="profile?.aliases.length" class="mt-2 flex flex-wrap items-center gap-1 text-xs text-slate-400">
              <Tag class="h-3 w-3" />
              <span v-for="alias in profile.aliases" :key="alias" class="chip bg-slate-500/10 text-slate-500 dark:text-zinc-400">{{ alias }}</span>
            </p>
            <a
              v-if="profile?.official_url"
              :href="profile.official_url"
              target="_blank"
              rel="noopener noreferrer"
              class="link mt-2 inline-flex items-center gap-1 text-xs"
            >
              <ExternalLink class="h-3 w-3" /> 官方页面
            </a>
            <p v-if="!profile" class="mt-2 text-xs text-slate-400">该商品还没有维护档案头（封面、官方链接、发售时间）。</p>
          </div>
        </div>
        <dl v-if="profile" class="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
          <div>
            <dt class="text-xs text-slate-400">当前观测价</dt>
            <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ fmtPrice(profile.price_current) }}</dd>
          </div>
          <div>
            <dt class="text-xs text-slate-400">价格观测时间</dt>
            <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ fmtTime(profile.price_observed_at) }}</dd>
          </div>
          <div>
            <dt class="text-xs text-slate-400">热度分</dt>
            <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ profile.heat_score }}</dd>
          </div>
          <div>
            <dt class="text-xs text-slate-400">曲线采样</dt>
            <dd class="mono mt-0.5 text-slate-700 dark:text-zinc-200">{{ series.length }} 天</dd>
          </div>
        </dl>
        <p class="mt-3 text-xs text-slate-400 dark:text-zinc-500">
          价格与热度均为公开来源的<b>观测值</b>，不是官方定价；下单前请以店铺页面为准。
        </p>
      </section>

      <!-- 价格 / 热度曲线 -->
      <section class="panel p-4 sm:p-5">
        <div class="flex items-baseline justify-between gap-3">
          <h3 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">价格 / 热度曲线</h3>
          <span v-if="points.length" class="mono text-xs text-slate-400">
            ¥{{ priceRange.min }} ~ ¥{{ priceRange.max }}
          </span>
        </div>
        <div v-if="series.length" class="mt-4 flex h-32 items-end gap-1" aria-label="价格与热度曲线">
          <div
            v-for="point in series"
            :key="point.date"
            class="group relative flex flex-1 flex-col items-center justify-end gap-1"
            :title="`${point.date} 价格 ${fmtPrice(point.price)} 热度 ${point.heat ?? 0} 命中 ${point.hits}`"
          >
            <div class="w-full rounded-sm bg-blue-500/15" :style="{ height: `${heatBar(point.heat)}%` }" />
            <div class="w-full rounded-sm bg-blue-500" :style="{ height: `${priceBar(point.price)}%` }" />
            <span class="mono text-[9px] text-slate-400">{{ fmtDate(point.date) }}</span>
          </div>
        </div>
        <p v-else class="mt-4 py-8 text-center text-sm text-slate-400">还没有价格观测记录。</p>
        <p class="mt-3 text-xs text-slate-400 dark:text-zinc-500">
          蓝色实心柱为当日观测价（越高越贵），浅色柱为热度（当日 + 近 7 日日均命中）。
        </p>
      </section>

      <!-- AI 质量反馈 -->
      <section class="panel p-4 sm:p-5">
        <h3 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">
          质量反馈（{{ dossier.feedback.length }}）
        </h3>
        <p class="mt-1 text-xs text-slate-400">
          AI 对命中正文的质量反馈做摘要，保留原文链接便于核对；未配置 AI 时以原文片段代替。
        </p>
        <ul class="mt-4 space-y-3">
          <li
            v-for="item in dossier.feedback"
            :key="item.id"
            class="rounded-lg border border-slate-900/5 p-3 dark:border-white/10"
          >
            <div class="flex flex-wrap items-center gap-2">
              <span class="chip" :class="sentimentTone[item.sentiment] ?? sentimentTone.negative">
                {{ item.sentiment_label }}
              </span>
              <span class="mono text-[11px] text-slate-400">{{ item.source_method === 'ai' ? 'AI 摘要' : '原文片段' }}</span>
              <span v-if="item.reviewed" class="chip bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">已确认</span>
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="link ml-auto text-xs">原文</a>
            </div>
            <p class="mt-2 text-[13px] leading-relaxed text-slate-700 dark:text-zinc-200">{{ item.summary }}</p>
            <p v-if="item.quote" class="mt-1 border-l-2 border-slate-900/10 pl-2 text-xs italic text-slate-500 dark:border-white/10 dark:text-zinc-400">
              “{{ item.quote }}”
            </p>
          </li>
        </ul>
        <p v-if="!dossier.feedback.length" class="mt-4 py-6 text-center text-sm text-slate-400">还没有质量反馈记录。</p>
      </section>

      <!-- 官方公告时间线 -->
      <section class="panel overflow-hidden">
        <h3 class="border-b border-slate-900/5 px-4 py-3 text-sm font-semibold text-slate-900 dark:border-white/10 dark:text-zinc-100">
          官方公告时间线（{{ dossier.timeline.length }}）
        </h3>
        <ul v-if="dossier.timeline.length" class="divide-y divide-slate-900/5 dark:divide-white/10">
          <li v-for="item in dossier.timeline" :key="item.event_id" class="px-4 py-3">
            <div class="flex flex-wrap items-center gap-2">
              <span v-if="item.importance" class="chip bg-amber-500/10 text-amber-700 dark:text-amber-300">重要</span>
              <span class="text-[13px] font-medium text-slate-800 dark:text-zinc-100">{{ item.title }}</span>
              <span class="mono ml-auto text-[11px] text-slate-400">{{ fmtTime(item.latest_evidence_at) }}</span>
            </div>
            <p v-if="item.summary" class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ item.summary }}</p>
            <p class="mt-1 text-[11px] text-slate-400">证据 {{ item.evidence_count }} 条</p>
          </li>
        </ul>
        <p v-else class="px-4 py-10 text-center text-sm text-slate-400">该商品暂无归并事件。</p>
      </section>
    </template>
  </div>
</template>
