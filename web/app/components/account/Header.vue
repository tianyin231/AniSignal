<script setup lang="ts">
import { BadgeCheck } from 'lucide-vue-next'
import type { AccountTab } from '~/composables/useAccountCenter'
import { MAX_KEYWORDS } from '~/composables/useAccountCenter'
import { avatarGradient, displayName } from '~/utils/avatars'
import { beijingParts, toTimestamp } from '~/utils/schedule'

// 账号中心页头：头像 + 昵称 + 四个状态数字（点了直接去对应标签页）。
defineEmits<{ select: [tab: AccountTab] }>()
const center = useAccountCenter()
const profile = computed(() => center.overview.value ?? center.user.value)
const name = computed(() => displayName(profile.value))
const joined = computed(() => {
  const ts = toTimestamp(profile.value?.created_at)
  if (!Number.isFinite(ts)) return ''
  const p = beijingParts(ts)
  return `${p.year}.${String(p.month).padStart(2, '0')}.${String(p.day).padStart(2, '0')} 加入`
})
const emailOn = computed(() => center.notifications.value?.enabled)
const pill = 'panel flex flex-col items-start gap-0.5 rounded-xl px-3 py-2.5 text-left transition-colors hover:border-faint/60 sm:flex-row sm:items-baseline sm:gap-1.5 sm:px-4'
</script>

<template>
  <section class="mb-5 flex flex-wrap items-end justify-between gap-4">
    <div class="wall-heading flex min-w-0 items-center gap-4">
      <span
        class="grid size-14 shrink-0 place-items-center rounded-2xl bg-gradient-to-b text-2xl font-bold text-white shadow-card"
        :class="avatarGradient(profile?.avatar)"
        aria-hidden="true"
      >{{ name[0]?.toUpperCase() }}</span>
      <div class="min-w-0">
        <p class="mono truncate text-xs tracking-wide text-muted">
          {{ profile?.email }}<template v-if="joined"> · {{ joined }}</template>
        </p>
        <h1 class="mt-1 flex items-center gap-2 text-[26px] font-bold leading-tight text-ink sm:text-[30px]">
          <span class="truncate">{{ name }}</span>
          <BadgeCheck v-if="profile?.is_verified" :size="22" class="shrink-0 text-ok" aria-label="邮箱已验证" />
        </h1>
      </div>
    </div>

    <div class="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:gap-2.5">
      <button type="button" :class="pill" @click="$emit('select', 'feed')">
        <span class="num text-xl font-bold text-ink sm:text-[22px]">
          {{ center.overview.value?.keywords_total ?? '—' }}<span class="font-medium text-faint">/{{ MAX_KEYWORDS }}</span>
        </span>
        <span class="text-xs text-muted sm:text-[13px]">个关键词</span>
      </button>
      <button type="button" :class="pill" @click="$emit('select', 'feed')">
        <span class="num text-xl font-bold text-ink sm:text-[22px]">{{ center.overview.value?.feed_total?.toLocaleString('zh-CN') ?? '—' }}</span>
        <span class="text-xs text-muted sm:text-[13px]">条命中</span>
      </button>
      <button type="button" :class="pill" @click="$emit('select', 'custom')">
        <span class="num text-xl font-bold text-ink sm:text-[22px]">
          {{ center.loading.custom ? '—' : center.customQuota.value.used }}<span class="font-medium text-faint">/{{ center.customQuota.value.max_feeds }}</span>
        </span>
        <span class="text-xs text-muted sm:text-[13px]">定制监控</span>
      </button>
      <button type="button" :class="pill" @click="$emit('select', 'notify')">
        <span class="flex items-center gap-1.5 text-base font-bold text-ink sm:text-[17px]">
          <span class="size-2 rounded-full" :class="emailOn ? 'bg-ok' : 'bg-faint'" aria-hidden="true" />
          {{ center.loading.notifications ? '—' : emailOn ? '已开启' : '未开启' }}
        </span>
        <span class="text-xs text-muted sm:text-[13px]">邮件通知</span>
      </button>
    </div>
  </section>
</template>
