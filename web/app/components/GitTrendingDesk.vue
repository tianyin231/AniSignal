<script setup lang="ts">
import { BookOpen, ChevronLeft, ChevronRight, Flame, Sparkles, Tag } from 'lucide-vue-next'
import { fmtTime } from '~/utils/admin-types'
import {
  formatPeriodKey,
  languageColor,
  periodShortLabel,
  trendingEntryToRawItem,
  type TrendingEditionsResponse,
  type TrendingPeriod,
} from '~/utils/git-trending'

const props = defineProps<{
  data: TrendingEditionsResponse
  loading?: boolean
}>()

const emit = defineEmits<{
  selectPeriod: [period: TrendingPeriod]
  selectIssue: [periodKey: string]
  selectTopic: [topic: string]
}>()

const onlyNew = ref(false)
const activeLanguage = ref('')

// 切换周期或期次时重置已不存在的语言筛选
watch(
  () => [props.data.active_period, props.data.current?.period_key],
  () => {
    if (activeLanguage.value && !(props.data.current?.languages ?? []).some(l => l.name === activeLanguage.value)) {
      activeLanguage.value = ''
    }
  },
)

const current = computed(() => props.data.current)
const activePeriod = computed<TrendingPeriod>(() => props.data.active_period)
const filteredRows = computed(() =>
  (current.value?.entries ?? [])
    .filter(entry => (!onlyNew.value || entry.status === 'new') && (!activeLanguage.value || entry.language === activeLanguage.value))
    .map(entry => ({ entry, item: trendingEntryToRawItem(entry) })),
)

const topLanguages = computed(() => (current.value?.languages ?? []).slice(0, 8))
const languageBarItems = computed(() =>
  topLanguages.value.map(l => ({ name: l.name, count: l.count, color: languageColor(l.name) })),
)

function toggleLanguage(lang: string) {
  activeLanguage.value = activeLanguage.value === lang ? '' : lang
}
</script>

<template>
  <div class="space-y-4">
    <!-- 刊头控制条：周期切换 + 期次翻阅 + 统计与语言筛选 -->
    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="inline-flex rounded-[10px] bg-chip p-[3px]" role="tablist" aria-label="刊物周期">
          <button
            v-for="p in data.periods"
            :key="p.period"
            type="button"
            role="tab"
            :aria-selected="activePeriod === p.period"
            :disabled="loading"
            class="inline-flex h-8 items-center gap-1.5 rounded-lg px-3.5 text-[13px] transition-colors disabled:opacity-50"
            :class="activePeriod === p.period ? 'bg-card font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'"
            @click="emit('selectPeriod', p.period)"
          >
            <span>{{ p.label }}</span>
            <span v-if="p.edition_count > 0" class="num text-[11px] opacity-70">{{ p.edition_count }}</span>
          </button>
        </div>

        <div v-if="current" class="flex items-center gap-1.5">
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1 rounded-lg border border-line bg-card px-2.5 text-xs text-ink-2 transition-colors hover:bg-chip hover:text-ink disabled:opacity-35"
            :disabled="loading || !current.prev_key"
            @click="current.prev_key && emit('selectIssue', current.prev_key)"
          >
            <ChevronLeft :size="14" aria-hidden="true" />
            <span class="hidden sm:inline">上一期</span>
          </button>
          <div class="flex items-center gap-2 rounded-lg bg-chip px-3 py-1.5 text-xs">
            <span class="font-semibold text-ink">第 {{ current.issue_number }} 期</span>
            <span class="text-faint">·</span>
            <span class="mono tabular-nums text-ink-2">{{ formatPeriodKey(activePeriod, current.period_key) }}</span>
          </div>
          <button
            type="button"
            class="inline-flex h-8 items-center gap-1 rounded-lg border border-line bg-card px-2.5 text-xs text-ink-2 transition-colors hover:bg-chip hover:text-ink disabled:opacity-35"
            :disabled="loading || !current.next_key"
            @click="current.next_key && emit('selectIssue', current.next_key)"
          >
            <span class="hidden sm:inline">下一期</span>
            <ChevronRight :size="14" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div v-if="current" class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line-soft pt-3.5">
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
          <span>本期 <strong class="num text-sm font-bold text-ink">{{ current.stats.total }}</strong> 项</span>
          <span class="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
            <Sparkles :size="12" aria-hidden="true" />首次上榜 <strong class="num text-sm font-bold">{{ current.stats.new_count }}</strong>
          </span>
          <span v-if="current.stats.streak_count > 0" class="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300">
            <Flame :size="12" aria-hidden="true" />连榜 <strong class="num font-semibold">{{ current.stats.streak_count }}</strong>
          </span>
          <span v-if="current.stats.returning_count > 0" class="text-sky-700 dark:text-sky-300">
            复榜 <strong class="num font-semibold">{{ current.stats.returning_count }}</strong>
          </span>
          <span class="mono tabular-nums text-faint">同步于 {{ fmtTime(current.captured_at) }}</span>
        </div>

        <button
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors"
          :class="onlyNew
            ? 'border-emerald-500/40 bg-emerald-500/12 text-emerald-700 dark:text-emerald-300'
            : 'border-line bg-card text-ink-2 hover:bg-chip hover:text-ink'"
          :aria-pressed="onlyNew"
          @click="onlyNew = !onlyNew"
        >
          <Sparkles :size="12" aria-hidden="true" />
          <span>仅看首次上榜</span>
        </button>
      </div>

      <div v-if="topLanguages.length > 1" class="mt-3 flex flex-wrap items-center gap-1.5" aria-label="编程语言筛选">
        <button
          type="button"
          class="h-7 rounded-full px-3 text-xs transition-colors"
          :class="!activeLanguage ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
          :aria-pressed="!activeLanguage"
          @click="activeLanguage = ''"
        >
          全部语言
        </button>
        <button
          v-for="lang in topLanguages"
          :key="lang.name"
          type="button"
          class="inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-xs transition-colors"
          :class="activeLanguage === lang.name ? 'bg-ink font-semibold text-card' : 'bg-chip text-ink-2 hover:text-ink'"
          :aria-pressed="activeLanguage === lang.name"
          @click="toggleLanguage(lang.name)"
        >
          <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: languageColor(lang.name) }" aria-hidden="true" />
          <span>{{ lang.name }}</span>
          <span class="num text-[11px] opacity-70">{{ lang.count }}</span>
        </button>
      </div>
    </section>

    <!-- 两栏阅读台：左侧复用 TopicFeedCard，右侧往期目录 / 语言分布 / 主仓库发版 -->
    <div v-if="current" class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div class="min-w-0 space-y-3" :class="{ 'opacity-65 transition-opacity': loading }">
        <template v-if="filteredRows.length">
          <TopicFeedCard
            v-for="row in filteredRows"
            :key="row.entry.repo"
            :item="row.item"
            :trending="{ entry: row.entry, period: activePeriod }"
            @select-topic="emit('selectTopic', $event)"
          />
        </template>
        <section v-else class="panel px-5 py-12 text-center text-sm text-muted">
          <p>当前筛选条件下暂无上榜项目</p>
          <button
            v-if="onlyNew || activeLanguage"
            type="button"
            class="mt-2 text-xs font-medium text-brand-ink hover:underline"
            @click="onlyNew = false; activeLanguage = ''"
          >
            重置筛选查看全部 {{ current.stats.total }} 项
          </button>
        </section>
      </div>

      <aside class="space-y-4 lg:sticky lg:top-[88px]">
        <section class="panel p-4">
          <div class="flex items-center justify-between">
            <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
              <BookOpen :size="14" class="text-accent-ink" aria-hidden="true" />
              {{ periodShortLabel(activePeriod) }}归档
            </h2>
            <span class="num text-xs text-faint">共 {{ data.editions.length }} 期</span>
          </div>
          <ul class="mt-3 max-h-72 space-y-1 overflow-y-auto pr-0.5">
            <li v-for="ed in data.editions" :key="ed.period_key">
              <button
                type="button"
                class="flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors"
                :class="current.period_key === ed.period_key
                  ? 'bg-brand-soft font-semibold text-brand-ink'
                  : 'text-ink-2 hover:bg-chip hover:text-ink'"
                @click="emit('selectIssue', ed.period_key)"
              >
                <span class="mono min-w-0 truncate tabular-nums">{{ formatPeriodKey(activePeriod, ed.period_key) }}</span>
                <span class="num shrink-0 text-[11px] opacity-80">{{ ed.item_count }} 项 · {{ ed.new_count }} 新</span>
              </button>
            </li>
          </ul>
        </section>

        <BarDistributionCard
          title="语言分布"
          :items="languageBarItems"
          :active="activeLanguage"
          :limit="6"
          @select="toggleLanguage"
          @reset="activeLanguage = ''"
        />

        <section v-if="data.releases.length" class="panel p-4">
          <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
            <Tag :size="14" class="text-accent-ink" aria-hidden="true" />
            主仓库发版
          </h2>
          <ul class="mt-3 divide-y divide-line-soft">
            <li v-for="rel in data.releases" :key="rel.id" class="py-2.5 first:pt-0 last:pb-0">
              <NuxtLink :to="`/contents/${rel.id}`" class="group block">
                <div class="flex items-center gap-1.5">
                  <span v-if="rel.tag_name" class="mono rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-ink">{{ rel.tag_name }}</span>
                  <span v-if="rel.prerelease" class="rounded bg-amber-500/12 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">预发布</span>
                  <span v-if="rel.published_at" class="mono ml-auto text-[11px] text-faint">{{ fmtTime(rel.published_at).slice(5, 10) }}</span>
                </div>
                <p class="mt-1 line-clamp-2 text-xs font-medium text-ink-2 group-hover:text-brand-ink">
                  {{ rel.brief?.headline || rel.title }}
                </p>
                <p v-if="rel.brief?.tagline" class="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-muted">
                  {{ rel.brief.tagline }}
                </p>
              </NuxtLink>
            </li>
          </ul>
        </section>
      </aside>
    </div>

    <section v-else class="panel px-5 py-12 text-center">
      <p class="text-sm font-medium text-ink">{{ periodShortLabel(activePeriod) }}首期尚未归档</p>
      <p class="mt-1 text-xs text-muted">完成一次「GitHub · 趋势榜单」采集后将自动生成本期榜单</p>
    </section>
  </div>
</template>
