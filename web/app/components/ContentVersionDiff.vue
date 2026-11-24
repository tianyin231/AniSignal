<script setup lang="ts">
import { ChevronDown, GitCompareArrows, History } from 'lucide-vue-next'
import type { ContentVersionHistory } from '~/composables/usePublicApi'
import { fmtTime } from '~/utils/admin-types'
import {
  canCompareWithFirst,
  diffStatsBadges,
  normalizeVersionPair,
  originLabel,
  pairForVersionStep,
  revisionBadgeLabel,
} from '~/utils/content-diff'
import { imageSrcset, imageUrl } from '~/utils/image'

const props = defineProps<{
  contentId: number
  revisionCount: number
  hasSourceRevision: boolean
  imageMode: 'show' | 'link' | 'hide'
  initialOpen?: boolean
}>()

const emit = defineEmits<{
  openImage: [url: string]
}>()

const { api } = usePublicApi()
const open = ref(Boolean(props.initialOpen))
const loading = ref(false)
const loadError = ref('')
const history = ref<ContentVersionHistory | null>(null)
const selectedFrom = ref<number | null>(null)
const selectedTo = ref<number | null>(null)
const brokenMedia = ref<Record<string, boolean>>({})

function markBroken(url: string) {
  if (!brokenMedia.value[url]) {
    brokenMedia.value = { ...brokenMedia.value, [url]: true }
  }
}

let activeController: AbortController | null = null

const headerBadge = computed(() => revisionBadgeLabel(props.revisionCount, props.hasSourceRevision))
const diff = computed(() => history.value?.diff ?? null)
const versions = computed(() => history.value?.versions ?? [])
const statBadges = computed(() => diffStatsBadges(diff.value?.stats))
const showCompareFirst = computed(() =>
  canCompareWithFirst(diff.value?.from_version, diff.value?.to_version, versions.value),
)

const statToneClass: Record<string, string> = {
  add: 'bg-emerald-500/12 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300',
  del: 'bg-red-500/12 text-red-700 dark:bg-red-400/15 dark:text-red-300',
  warn: 'bg-amber-500/12 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300',
  neutral: 'bg-chip text-ink-2',
}

async function fetchDiff(fromVersion?: number | null, toVersion?: number | null) {
  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  loading.value = true
  loadError.value = ''
  try {
    const query: Record<string, number> = {}
    if (fromVersion != null) query.from_version = fromVersion
    if (toVersion != null) query.to_version = toVersion
    const data = await api<ContentVersionHistory>(`/api/contents/${props.contentId}/versions`, {
      query,
      signal: controller.signal,
    })
    history.value = data
    if (data.diff) {
      selectedFrom.value = data.diff.from_version
      selectedTo.value = data.diff.to_version
    }
  } catch (err: any) {
    if (err?.name === 'AbortError' || controller.signal.aborted) return
    loadError.value = '版本对比加载失败'
  } finally {
    if (activeController === controller) {
      loading.value = false
    }
  }
}

function toggleOpen() {
  open.value = !open.value
  if (open.value && !history.value && !loading.value) {
    fetchDiff()
  }
}

function expandAndLoad() {
  open.value = true
  if (!history.value && !loading.value) {
    fetchDiff()
  }
}

defineExpose({ expandAndLoad })

function onPairChange(nextFrom: number, nextTo: number) {
  const pair = normalizeVersionPair(nextFrom, nextTo, versions.value)
  if (!pair) return
  selectedFrom.value = pair.from
  selectedTo.value = pair.to
  fetchDiff(pair.from, pair.to)
}

function compareWithFirst() {
  const first = versions.value[0]?.version
  const latest = versions.value[versions.value.length - 1]?.version
  if (first != null && latest != null && first !== latest) {
    onPairChange(first, latest)
  }
}

function selectVersionStep(version: number) {
  const pair = pairForVersionStep(version, versions.value)
  if (!pair) return
  onPairChange(pair.from, pair.to)
}

function shortDate(iso: string) {
  const full = fmtTime(iso)
  return full.length >= 10 ? full.slice(5, 10) : full
}

function shortTime(iso: string) {
  const full = fmtTime(iso)
  return full.length >= 16 ? full.slice(5) : full
}

onMounted(() => {
  if (open.value) {
    fetchDiff()
  }
})

onBeforeUnmount(() => {
  activeController?.abort()
})

watch(() => props.contentId, () => {
  history.value = null
  selectedFrom.value = null
  selectedTo.value = null
  brokenMedia.value = {}
  if (open.value) {
    fetchDiff()
  }
})
</script>

<template>
  <section
    id="revisions"
    class="panel overflow-hidden scroll-mt-20"
    :class="hasSourceRevision ? 'border-amber-500/30 dark:border-amber-400/25' : ''"
    aria-labelledby="revisions-heading"
  >
    <button
      type="button"
      class="flex w-full items-center justify-between gap-3 px-4 py-3.5 text-left transition-colors hover:bg-line-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand sm:px-5"
      :aria-expanded="open"
      aria-controls="revisions-body"
      @click="toggleOpen"
    >
      <div class="flex min-w-0 flex-wrap items-center gap-2">
        <GitCompareArrows
          :size="17"
          class="shrink-0"
          :class="hasSourceRevision ? 'text-amber-600 dark:text-amber-400' : 'text-muted'"
          aria-hidden="true"
        />
        <h2 id="revisions-heading" class="text-base font-bold text-ink">修订对比</h2>
        <span
          v-if="headerBadge"
          class="inline-flex h-5 items-center rounded-md px-1.5 text-xs font-semibold"
          :class="hasSourceRevision ? 'bg-amber-500/12 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300' : 'bg-chip text-ink-2'"
        >
          {{ headerBadge }}
        </span>
        <span
          v-for="badge in statBadges"
          :key="badge.key"
          class="mono inline-flex h-5 items-center rounded-md px-1.5 text-[11px] font-semibold"
          :class="statToneClass[badge.tone]"
        >
          {{ badge.label }}
        </span>
      </div>
      <ChevronDown
        :size="17"
        class="shrink-0 text-muted transition-transform duration-200"
        :class="open ? 'rotate-180' : ''"
        aria-hidden="true"
      />
    </button>

    <div v-if="open" id="revisions-body" class="border-t border-line-soft px-4 py-4 sm:px-5">
      <div v-if="loading && !diff" class="py-4 text-center text-sm text-muted">
        加载版本对比中…
      </div>

      <div v-else-if="loadError" role="alert" class="flex items-center justify-between gap-2 rounded-lg bg-amber-500/10 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-300">
        <span>{{ loadError }}</span>
        <button type="button" class="font-semibold underline" @click="fetchDiff(selectedFrom, selectedTo)">重试</button>
      </div>

      <div v-else-if="diff" class="space-y-4">
        <!-- 版本切换栏 -->
        <div class="flex flex-wrap items-center justify-between gap-2.5 text-xs">
          <div v-if="versions.length >= 3" class="flex flex-wrap items-center gap-2">
            <label class="inline-flex items-center gap-1.5">
              <span class="sr-only">基准版本</span>
              <select
                :value="selectedFrom ?? diff.from_version"
                :disabled="loading"
                aria-label="基准版本"
                class="input mono h-8 rounded-lg px-2 text-xs"
                @change="onPairChange(Number(($event.target as HTMLSelectElement).value), selectedTo ?? diff.to_version)"
              >
                <option
                  v-for="v in versions"
                  :key="`from-${v.version}`"
                  :value="v.version"
                  :disabled="v.version === (selectedTo ?? diff.to_version)"
                >
                  v{{ v.version }} · {{ shortTime(v.parsed_at) }} · {{ originLabel(v.origin) }}
                </option>
              </select>
            </label>
            <span class="text-faint" aria-hidden="true">→</span>
            <label class="inline-flex items-center gap-1.5">
              <span class="sr-only">目标版本</span>
              <select
                :value="selectedTo ?? diff.to_version"
                :disabled="loading"
                aria-label="目标版本"
                class="input mono h-8 rounded-lg px-2 text-xs"
                @change="onPairChange(selectedFrom ?? diff.from_version, Number(($event.target as HTMLSelectElement).value))"
              >
                <option
                  v-for="v in versions"
                  :key="`to-${v.version}`"
                  :value="v.version"
                  :disabled="v.version === (selectedFrom ?? diff.from_version)"
                >
                  v{{ v.version }} · {{ shortTime(v.parsed_at) }} · {{ originLabel(v.origin) }}
                </option>
              </select>
            </label>
            <button
              v-if="showCompareFirst"
              type="button"
              :disabled="loading"
              class="inline-flex h-8 items-center gap-1 rounded-lg border border-line bg-card px-2.5 text-xs font-medium text-ink-2 transition-colors hover:bg-chip hover:text-ink"
              @click="compareWithFirst"
            >
              <History :size="13" aria-hidden="true" />
              对比首版
            </button>
          </div>
          <div v-else class="mono flex flex-wrap items-center gap-1.5 text-muted">
            <span>v{{ diff.from_version }} ({{ shortTime(diff.from_parsed_at) }})</span>
            <span aria-hidden="true">→</span>
            <span class="font-semibold text-ink">v{{ diff.to_version }} ({{ shortTime(diff.to_parsed_at) }})</span>
          </div>

          <span
            class="inline-flex h-6 items-center rounded-md px-2 text-[11px] font-semibold"
            :class="diff.to_origin === 'source' ? 'bg-amber-500/12 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300' : 'bg-chip text-muted'"
          >
            {{ originLabel(diff.to_origin) }}
          </span>
        </div>

        <!-- 多版本滚动更新快捷时间线（≥3 版本时显示，一键切任意一次增量修订） -->
        <div v-if="versions.length >= 3" class="flex items-center gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:none]" role="group" aria-label="版本时间线">
          <button
            v-for="(v, idx) in versions"
            :key="`step-${v.version}`"
            type="button"
            :disabled="loading"
            class="mono inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-[11px] transition-colors"
            :class="
              (idx === 0 && diff.from_version === v.version && diff.to_version === versions[versions.length - 1]?.version)
                || (idx > 0 && diff.to_version === v.version && diff.from_version === versions[idx - 1]?.version)
                ? 'border-brand/40 bg-brand-soft font-semibold text-brand-ink'
                : 'border-line-soft bg-card/70 text-muted hover:bg-chip hover:text-ink'
            "
            @click="selectVersionStep(v.version)"
          >
            <span
              class="size-1.5 rounded-full"
              :class="idx === 0 ? 'bg-faint' : v.origin === 'source' ? 'bg-amber-500' : 'bg-sky-500'"
              aria-hidden="true"
            />
            <span>{{ idx === 0 ? `v${v.version} 首版` : `v${v.version}` }}</span>
            <span class="text-faint">{{ shortDate(v.parsed_at) }}</span>
          </button>
        </div>

        <!-- 标题变更 -->
        <div v-if="diff.title_changed && diff.title_segments.length" class="rounded-xl border border-line-soft bg-chip/40 px-3.5 py-2.5">
          <div class="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">标题变更</div>
          <p class="break-words text-sm leading-relaxed text-ink">
            <template v-for="(seg, idx) in diff.title_segments" :key="idx">
              <del
                v-if="seg.op === 'delete'"
                class="rounded-xs bg-red-500/18 px-0.5 text-red-800 decoration-red-500/70 dark:bg-red-500/25 dark:text-red-200"
              >{{ seg.text }}</del>
              <ins
                v-else-if="seg.op === 'insert'"
                class="rounded-xs bg-emerald-500/18 px-0.5 font-medium text-emerald-900 no-underline dark:bg-emerald-500/25 dark:text-emerald-100"
              >{{ seg.text }}</ins>
              <span v-else>{{ seg.text }}</span>
            </template>
          </p>
        </div>

        <!-- 正文差异块 -->
        <div v-if="diff.hunks.length" class="overflow-hidden rounded-xl border border-line-soft bg-card/70">
          <template v-for="(hunk, hIdx) in diff.hunks" :key="`${hunk.old_start}-${hunk.new_start}-${hIdx}`">
            <div
              v-if="hIdx > 0"
              class="mono border-y border-line-soft bg-chip/60 px-3 py-1 text-center text-[11px] tracking-widest text-faint"
              aria-hidden="true"
            >
              ···
            </div>
            <div class="divide-y divide-line-soft/50 text-[13px] leading-relaxed">
              <div
                v-for="(line, lIdx) in hunk.lines"
                :key="`${hIdx}-${lIdx}`"
                class="flex items-start gap-2 px-2.5 py-1.5 sm:px-3"
                :class="{
                  'bg-red-500/8 text-ink dark:bg-red-500/12': line.op === 'delete',
                  'bg-emerald-500/8 text-ink dark:bg-emerald-500/12': line.op === 'insert',
                  'text-muted': line.op === 'equal',
                }"
              >
                <span class="mono hidden w-7 shrink-0 select-none text-right text-[11px] text-faint sm:inline-block">{{ line.old_no ?? '' }}</span>
                <span class="mono hidden w-7 shrink-0 select-none text-right text-[11px] text-faint sm:inline-block">{{ line.new_no ?? '' }}</span>
                <span
                  class="mono w-3.5 shrink-0 select-none text-center font-bold"
                  :class="{
                    'text-red-600 dark:text-red-400': line.op === 'delete',
                    'text-emerald-600 dark:text-emerald-400': line.op === 'insert',
                    'text-faint': line.op === 'equal',
                  }"
                  aria-hidden="true"
                >{{ line.op === 'delete' ? '-' : line.op === 'insert' ? '+' : ' ' }}</span>
                <span class="min-w-0 flex-1 break-words whitespace-pre-wrap">
                  <template v-if="line.segments?.length">
                    <template v-for="(seg, sIdx) in line.segments" :key="sIdx">
                      <del
                        v-if="seg.op === 'delete'"
                        class="rounded-xs bg-red-500/22 px-0.5 text-red-900 decoration-red-500/70 dark:bg-red-400/30 dark:text-red-100"
                      >{{ seg.text }}</del>
                      <ins
                        v-else-if="seg.op === 'insert'"
                        class="rounded-xs bg-emerald-500/22 px-0.5 font-medium text-emerald-950 no-underline dark:bg-emerald-400/30 dark:text-emerald-100"
                      >{{ seg.text }}</ins>
                      <span v-else>{{ seg.text }}</span>
                    </template>
                  </template>
                  <template v-else>{{ line.text }}</template>
                </span>
              </div>
            </div>
          </template>
          <p v-if="diff.truncated" class="border-t border-line-soft bg-chip/50 px-3 py-2 text-xs text-muted">
            差异行数较多，仅展示前 300 行。
          </p>
        </div>

        <!-- 图片增删 -->
        <div
          v-if="imageMode !== 'hide' && (diff.media_added.length || diff.media_removed.length)"
          class="space-y-3"
        >
          <div v-if="diff.media_added.length">
            <div class="mb-1.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300">
              新增图片（{{ diff.media_added.length }}）
            </div>
            <div v-if="imageMode === 'show'" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <template v-for="(m, idx) in diff.media_added" :key="`add-${m}`">
                <a
                  v-if="brokenMedia[m]"
                  :href="m"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-emerald-500/40 bg-emerald-500/5 px-2 text-center text-[11px] text-emerald-700 hover:underline dark:text-emerald-300"
                >
                  <span>图片加载失败</span>
                  <span class="text-[10px] opacity-75">打开原图 #{{ idx + 1 }}</span>
                </a>
                <button
                  v-else
                  type="button"
                  aria-label="查看新增图片"
                  class="overflow-hidden rounded-lg border-2 border-emerald-500/45 transition-opacity hover:opacity-90"
                  @click="emit('openImage', m)"
                >
                  <img
                    :src="imageUrl(m, 320)"
                    :srcset="imageSrcset(m, [160, 320])"
                    sizes="(min-width: 640px) 25vw, 50vw"
                    alt=""
                    referrerpolicy="no-referrer"
                    loading="lazy"
                    decoding="async"
                    class="aspect-video w-full object-cover"
                    @error="markBroken(m)"
                  >
                </button>
              </template>
            </div>
            <div v-else class="flex flex-wrap gap-2">
              <a
                v-for="(m, idx) in diff.media_added"
                :key="`add-link-${m}`"
                :href="m"
                target="_blank"
                rel="noopener noreferrer"
                class="rounded-lg border border-emerald-500/35 px-2.5 py-1 text-xs text-emerald-700 hover:underline dark:text-emerald-300"
              >
                新增图片 #{{ idx + 1 }}
              </a>
            </div>
          </div>

          <div v-if="diff.media_removed.length">
            <div class="mb-1.5 text-xs font-semibold text-red-700 dark:text-red-300">
              移除图片（{{ diff.media_removed.length }}）
            </div>
            <div v-if="imageMode === 'show'" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <template v-for="(m, idx) in diff.media_removed" :key="`rem-${m}`">
                <a
                  v-if="brokenMedia[m]"
                  :href="m"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-red-500/35 bg-red-500/5 px-2 text-center text-[11px] text-red-700/80 hover:underline dark:text-red-300/80"
                >
                  <span>原图已被源站移除</span>
                  <span class="text-[10px] opacity-75">原链接 #{{ idx + 1 }}</span>
                </a>
                <button
                  v-else
                  type="button"
                  aria-label="查看移除图片"
                  class="overflow-hidden rounded-lg border-2 border-red-500/45 opacity-80 transition-opacity hover:opacity-100"
                  @click="emit('openImage', m)"
                >
                  <img
                    :src="imageUrl(m, 320)"
                    :srcset="imageSrcset(m, [160, 320])"
                    sizes="(min-width: 640px) 25vw, 50vw"
                    alt=""
                    referrerpolicy="no-referrer"
                    loading="lazy"
                    decoding="async"
                    class="aspect-video w-full object-cover"
                    @error="markBroken(m)"
                  >
                </button>
              </template>
            </div>
            <div v-else class="flex flex-wrap gap-2">
              <a
                v-for="(m, idx) in diff.media_removed"
                :key="`rem-link-${m}`"
                :href="m"
                target="_blank"
                rel="noopener noreferrer"
                class="rounded-lg border border-red-500/35 px-2.5 py-1 text-xs text-red-700 hover:underline dark:text-red-300"
              >
                移除图片 #{{ idx + 1 }}
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
