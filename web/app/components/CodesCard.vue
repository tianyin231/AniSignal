<script setup lang="ts">
import { Check, CheckCircle2, Circle, Copy } from 'lucide-vue-next'
import type { RedeemCodeDto } from '~/composables/usePublicApi'
import { communityClasses, communityMeta } from '~/composables/useGameSection'
import { codeServerLabel, codeSourceBadge, formatCodeExpiry } from '~/utils/calendar-state'
import { copyText } from '~/utils/clipboard'

// 兑换码：官方 + 社区 UP 主双轨抽取，点击复制，支持「已领」个人打卡（访客本地 + 账号同步）
const props = defineProps<{ codes: RedeemCodeDto[], showCommunity?: boolean }>()
const { isCodeClaimed, toggleCodeClaimed, timezone } = useCalendarState()
const copied = ref('')
const hideClaimed = ref(false)
const nowMs = useState('codes-now-ms', () => Date.now())
onMounted(() => { nowMs.value = Date.now() })

async function copy(code: string) {
  if (await copyText(code)) {
    copied.value = code
    setTimeout(() => { if (copied.value === code) copied.value = '' }, 1500)
  }
}

const rows = computed(() => props.codes
  .map((c) => {
    const meta = props.showCommunity ? communityMeta(c.community) : undefined
    const claimed = isCodeClaimed(c.code, c.community)
    const source = codeSourceBadge(c)
    const server = codeServerLabel(c.server)
    const expiry = formatCodeExpiry(c, timezone.value, nowMs.value)
    return {
      ...c,
      game: meta?.label,
      chip: meta ? communityClasses(meta.color).chip : '',
      claimed,
      source,
      server,
      expiry,
    }
  })
  .sort((a, b) => Number(a.claimed) - Number(b.claimed)))

const claimedCount = computed(() => rows.value.filter(r => r.claimed).length)
const visibleRows = computed(() => hideClaimed.value ? rows.value.filter(r => !r.claimed) : rows.value)

function sourceBadgeClass(tone: 'official' | 'verified' | 'community') {
  if (tone === 'official') return 'bg-brand-soft text-brand-ink'
  if (tone === 'verified') return 'bg-ok/12 text-ok'
  return 'bg-chip text-muted'
}
</script>

<template>
  <section v-if="rows.length" id="codes" class="panel px-5 pb-2 pt-4" aria-labelledby="codes-title">
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-baseline gap-2">
        <h2 id="codes-title" class="text-base font-bold text-ink">兑换码</h2>
        <span v-if="claimedCount > 0" class="text-[11px] tabular-nums text-muted">
          已领 {{ claimedCount }} / {{ rows.length }}
        </span>
      </div>
      <button
        v-if="claimedCount > 0"
        type="button"
        class="rounded-md px-2 py-1 text-[11px] font-medium text-muted transition-colors hover:bg-chip hover:text-ink"
        :aria-pressed="hideClaimed"
        @click="hideClaimed = !hideClaimed"
      >
        {{ hideClaimed ? `显示已领 (${claimedCount})` : '隐藏已领' }}
      </button>
    </div>
    <p v-if="!visibleRows.length" class="border-t border-line-soft py-4 text-center text-xs text-muted">
      当前展示的兑换码已全部标记领取。
    </p>
    <ul v-else class="mt-2">
      <li
        v-for="c in visibleRows"
        :key="`${c.community}-${c.code}`"
        class="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 border-t border-line-soft py-2.5 transition-opacity"
        :class="{ 'opacity-55': c.claimed }"
      >
        <button
          type="button"
          class="mono inline-flex h-9 max-w-full shrink-0 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition-colors"
          :class="c.claimed
            ? 'bg-chip text-muted line-through hover:bg-chip/80'
            : 'bg-brand-soft text-brand-ink hover:bg-brand/20'"
          :aria-label="copied === c.code ? '已复制' : `复制兑换码 ${c.code}`"
          @click="copy(c.code)"
        >
          <span class="truncate">{{ c.code }}</span>
          <Check v-if="copied === c.code" :size="14" class="shrink-0" aria-hidden="true" />
          <Copy v-else :size="13" class="shrink-0 opacity-60" aria-hidden="true" />
        </button>

        <div class="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 text-xs text-muted">
          <span v-if="c.game" class="rounded px-1.5 py-0.5 text-[10px] font-semibold" :class="c.chip">{{ c.game }}</span>
          <span class="rounded px-1.5 py-0.5 text-[10px] font-semibold" :class="sourceBadgeClass(c.source.tone)">
            {{ c.source.label }}
          </span>
          <span v-if="c.server" class="rounded border border-line px-1.5 py-0.5 text-[10px] font-medium text-ink-2">
            {{ c.server }}
          </span>
          <span v-if="c.reward" class="truncate text-ink-2">{{ c.reward }}</span>
          <span class="tabular-nums" :class="{ 'font-semibold text-hot': c.expiry.urgent && !c.claimed }">
            {{ c.expiry.text }}
          </span>
        </div>

        <button
          type="button"
          class="inline-flex h-8 shrink-0 items-center gap-1 rounded-full border px-2.5 text-[11px] font-medium transition-colors"
          :class="c.claimed
            ? 'border-ok/40 bg-ok/10 text-ok'
            : 'border-line text-muted hover:border-brand/40 hover:bg-chip hover:text-ink'"
          :aria-pressed="c.claimed"
          :aria-label="c.claimed ? `取消兑换码 ${c.code} 的已领标记` : `标记兑换码 ${c.code} 为已领`"
          @click="toggleCodeClaimed(c.code, c.community)"
        >
          <CheckCircle2 v-if="c.claimed" :size="13" aria-hidden="true" />
          <Circle v-else :size="13" aria-hidden="true" />
          {{ c.claimed ? '已领' : '标记已领' }}
        </button>
        <a v-if="c.url" :href="c.url" target="_blank" rel="noopener noreferrer" class="link shrink-0 text-xs">来源</a>
      </li>
    </ul>
  </section>
</template>
