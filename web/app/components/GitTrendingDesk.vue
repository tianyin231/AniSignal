<script setup lang="ts">
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Flame,
  GitFork,
  Sparkles,
  Star,
  Tag,
} from 'lucide-vue-next'
import { fmtTime } from '~/utils/admin-types'
import {
  formatCompactStars,
  formatPeriodKey,
  languageColor,
  periodGainLabel,
  periodShortLabel,
  splitRepoName,
  trendingStatusBadge,
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
}>()

const onlyNew = ref(false)
const activeLanguage = ref('')

// 切换周期或期次时重置语言筛选，避免切到没有该语言的期次出现误空
watch(
  () => [props.data.active_period, props.data.current?.period_key],
  () => {
    if (
      activeLanguage.value
      && !(props.data.current?.languages ?? []).some(l => l.name === activeLanguage.value)
    ) {
      activeLanguage.value = ''
    }
  },
)

const current = computed(() => props.data.current)
const activePeriod = computed<TrendingPeriod>(() => props.data.active_period)
const allEntries = computed(() => current.value?.entries ?? [])

const filteredEntries = computed(() =>
  allEntries.value.filter((entry) => {
    if (onlyNew.value && entry.status !== 'new') return false
    if (activeLanguage.value && entry.language !== activeLanguage.value) return false
    return true
  }),
)

const topLanguages = computed(() => (current.value?.languages ?? []).slice(0, 8))
const maxLangCount = computed(() => Math.max(1, ...(topLanguages.value.map(l => l.count))))

const badgeToneClass: Record<'new' | 'streak' | 'returning', string> = {
  new: 'bg-emerald-500/12 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300',
  streak: 'bg-amber-500/12 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300',
  returning: 'bg-sky-500/12 text-sky-700 dark:bg-sky-400/15 dark:text-sky-300',
}

function toggleLanguage(lang: string) {
  activeLanguage.value = activeLanguage.value === lang ? '' : lang
}
</script>

<template>
  <div class="space-y-4">
    <!-- 刊头控制条：周期切换 + 期次翻阅 + 统计摘要 -->
    <section class="panel p-4 sm:p-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <!-- 日刊 / 周刊 / 月刊 -->
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

        <!-- 期次翻阅 -->
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

      <!-- 本期统计与筛选条 -->
      <div v-if="current" class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line-soft pt-3.5">
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
          <span>
            本期 <strong class="num text-sm font-bold text-ink">{{ current.stats.total }}</strong> 项
          </span>
          <span class="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
            <Sparkles :size="12" aria-hidden="true" />
            首次上榜 <strong class="num text-sm font-bold">{{ current.stats.new_count }}</strong>
          </span>
          <span v-if="current.stats.streak_count > 0" class="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300">
            <Flame :size="12" aria-hidden="true" />
            连榜 <strong class="num font-semibold">{{ current.stats.streak_count }}</strong>
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

      <!-- 编程语言筛选胶囊 -->
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

    <!-- 主内容两栏布局：左侧榜单 + 右侧往期目录/语言分布/主仓库发版 -->
    <div v-if="current" class="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
      <!-- 榜单列表 -->
      <section class="panel overflow-hidden" :class="{ 'opacity-65 transition-opacity': loading }">
        <ol v-if="filteredEntries.length" class="divide-y divide-line-soft">
          <li
            v-for="entry in filteredEntries"
            :key="entry.repo"
            class="group flex items-start gap-3.5 px-4 py-4 transition-colors hover:bg-line-soft/70 sm:gap-4 sm:px-5"
          >
            <!-- 排名与升降 -->
            <div class="flex w-9 shrink-0 flex-col items-center pt-0.5 text-center">
              <span
                class="num text-lg font-bold leading-none tabular-nums"
                :class="entry.rank <= 3 ? 'text-accent-ink' : 'text-faint'"
              >{{ String(entry.rank).padStart(2, '0') }}</span>
              <span
                v-if="entry.rank_delta !== null && entry.rank_delta !== 0"
                class="num mt-1 inline-flex items-center text-[11px] font-semibold tabular-nums"
                :class="entry.rank_delta > 0 ? 'text-ok' : 'text-faint'"
                :title="entry.rank_delta > 0 ? `较上期上升 ${entry.rank_delta} 位` : `较上期下降 ${Math.abs(entry.rank_delta)} 位`"
              >
                <ArrowUp v-if="entry.rank_delta > 0" :size="10" aria-hidden="true" />
                <ArrowDown v-else :size="10" aria-hidden="true" />
                {{ Math.abs(entry.rank_delta) }}
              </span>
            </div>

            <!-- 仓库主体信息 -->
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-x-2 gap-y-1.5">
                <a
                  :href="entry.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="inline-flex min-w-0 max-w-full items-baseline gap-1 text-[15px] leading-snug text-ink transition-colors hover:text-brand-ink"
                >
                  <span v-if="splitRepoName(entry.repo).owner" class="mono truncate text-xs text-muted">{{ splitRepoName(entry.repo).owner }} /</span>
                  <span class="truncate font-bold">{{ splitRepoName(entry.repo).name }}</span>
                  <ArrowUpRight :size="13" class="shrink-0 text-faint opacity-70 transition-opacity group-hover:opacity-100" aria-hidden="true" />
                </a>

                <span
                  class="inline-flex h-5 items-center rounded-md px-1.5 text-[11px] font-semibold"
                  :class="badgeToneClass[trendingStatusBadge(entry, activePeriod).tone]"
                >
                  {{ trendingStatusBadge(entry, activePeriod).label }}
                </span>

                <span
                  v-for="other in entry.also_in"
                  :key="other"
                  class="inline-flex h-5 items-center rounded-md bg-chip px-1.5 text-[11px] text-ink-2"
                >
                  同步进{{ periodShortLabel(other) }}
                </span>
              </div>

              <p v-if="entry.description" class="mt-1.5 line-clamp-2 text-[13px] leading-relaxed text-ink-2">
                {{ entry.description }}
              </p>

              <div class="mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-xs text-muted">
                <span v-if="entry.language" class="inline-flex items-center gap-1.5 text-ink-2">
                  <span class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: languageColor(entry.language) }" aria-hidden="true" />
                  <span>{{ entry.language }}</span>
                </span>
                <span v-if="entry.stars > 0" class="num inline-flex items-center gap-1 tabular-nums" :title="`总计 ${entry.stars.toLocaleString('zh-CN')} Star`">
                  <Star :size="12" class="text-faint" aria-hidden="true" />
                  {{ formatCompactStars(entry.stars) }}
                </span>
                <span v-if="entry.forks > 0" class="num inline-flex items-center gap-1 tabular-nums" :title="`${entry.forks.toLocaleString('zh-CN')} Fork`">
                  <GitFork :size="12" class="text-faint" aria-hidden="true" />
                  {{ formatCompactStars(entry.forks) }}
                </span>
                <span v-if="entry.period_stars > 0" class="num font-semibold text-ok sm:hidden">
                  +{{ entry.period_stars.toLocaleString('zh-CN') }} {{ periodGainLabel(activePeriod) }}
                </span>
                <span v-if="entry.appearances > 1" class="mono text-faint">
                  首见 {{ entry.first_seen_key }} · 最高 #{{ entry.best_rank }}
                </span>
                <NuxtLink
                  v-if="entry.raw_content_id"
                  :to="`/contents/${entry.raw_content_id}`"
                  class="ml-auto text-xs text-faint transition-colors hover:text-brand-ink"
                >
                  站内档案
                </NuxtLink>
              </div>
            </div>

            <!-- 右侧新增星标列 -->
            <div v-if="entry.period_stars > 0" class="hidden w-24 shrink-0 pt-0.5 text-right sm:block">
              <div class="num text-[15px] font-bold tabular-nums text-ok">
                +{{ entry.period_stars.toLocaleString('zh-CN') }}
              </div>
              <div class="mt-0.5 text-[11px] text-faint">{{ periodGainLabel(activePeriod) }}</div>
            </div>
          </li>
        </ol>

        <div v-else class="px-5 py-12 text-center text-sm text-muted">
          <p>当前筛选条件下暂无上榜项目</p>
          <button
            v-if="onlyNew || activeLanguage"
            type="button"
            class="mt-2 text-xs font-medium text-brand-ink hover:underline"
            @click="onlyNew = false; activeLanguage = ''"
          >
            重置筛选查看全部 {{ current.stats.total }} 项
          </button>
        </div>
      </section>

      <!-- 右侧栏：往期目录 + 语言分布 + 主仓库发版 -->
      <aside class="space-y-4">
        <!-- 往期目录 -->
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
                <span class="min-w-0 truncate">
                  <span class="mono tabular-nums">{{ formatPeriodKey(activePeriod, ed.period_key) }}</span>
                </span>
                <span class="num shrink-0 text-[11px] opacity-80">
                  {{ ed.item_count }} 项 · {{ ed.new_count }} 新
                </span>
              </button>
            </li>
          </ul>
        </section>

        <!-- 本期语言分布 -->
        <section v-if="topLanguages.length" class="panel p-4">
          <h2 class="text-sm font-bold text-ink">语言分布</h2>
          <ul class="mt-3 space-y-2.5">
            <li v-for="lang in topLanguages.slice(0, 6)" :key="lang.name">
              <button
                type="button"
                class="group w-full text-left"
                @click="toggleLanguage(lang.name)"
              >
                <div class="flex items-center justify-between text-xs">
                  <span class="inline-flex items-center gap-1.5 font-medium" :class="activeLanguage === lang.name ? 'text-brand-ink' : 'text-ink-2 group-hover:text-ink'">
                    <span class="size-2 rounded-full" :style="{ backgroundColor: languageColor(lang.name) }" aria-hidden="true" />
                    {{ lang.name }}
                  </span>
                  <span class="num tabular-nums text-muted">{{ lang.count }}</span>
                </div>
                <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-chip">
                  <div
                    class="h-full rounded-full transition-all"
                    :style="{
                      width: `${Math.max(8, Math.round((lang.count / maxLangCount) * 100))}%`,
                      backgroundColor: languageColor(lang.name),
                    }"
                  />
                </div>
              </button>
            </li>
          </ul>
        </section>

        <!-- 主仓库动态 -->
        <section v-if="data.releases.length" class="panel p-4">
          <div class="flex items-center justify-between">
            <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
              <Tag :size="14" class="text-accent-ink" aria-hidden="true" />
              主仓库发版
            </h2>
          </div>
          <ul class="mt-3 divide-y divide-line-soft">
            <li v-for="rel in data.releases" :key="rel.id" class="py-2.5 first:pt-0 last:pb-0">
              <NuxtLink :to="`/contents/${rel.id}`" class="group block">
                <div class="flex items-center gap-1.5">
                  <span v-if="rel.tag_name" class="mono rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-ink">{{ rel.tag_name }}</span>
                  <span v-if="rel.prerelease" class="rounded bg-amber-500/12 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">预发布</span>
                  <span v-if="rel.published_at" class="mono ml-auto text-[11px] text-faint">{{ fmtTime(rel.published_at).slice(5, 10) }}</span>
                </div>
                <p class="mt-1 line-clamp-2 text-xs font-medium text-ink-2 group-hover:text-brand-ink">{{ rel.title }}</p>
              </NuxtLink>
            </li>
          </ul>
        </section>
      </aside>
    </div>

    <!-- 当前周期尚未采集首期 -->
    <section v-else class="panel px-5 py-12 text-center">
      <p class="text-sm font-medium text-ink">{{ periodShortLabel(activePeriod) }}首期尚未归档</p>
      <p class="mt-1 text-xs text-muted">完成一次「GitHub · 趋势榜单」采集后将自动生成本期榜单</p>
    </section>
  </div>
</template>
