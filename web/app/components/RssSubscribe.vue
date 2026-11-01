<script setup lang="ts">
import { Check, Copy, Rss } from 'lucide-vue-next'
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

// RSS 订阅：选范围 → 复制地址到阅读器。不需要登录。
withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })
const community = ref('all')
const origin = ref('')
onMounted(() => { origin.value = window.location.origin })
const path = computed(() => `/rss.xml?community=${community.value}`)
const url = computed(() => `${origin.value}${path.value}`)
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
async function copy() {
  try {
    await navigator.clipboard.writeText(url.value)
    copied.value = true
    clearTimeout(timer)
    timer = setTimeout(() => { copied.value = false }, 2000)
  } catch {
    copied.value = false
  }
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section class="panel p-5" aria-labelledby="rss-title">
    <header class="flex items-start gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-warn/10 text-warn" aria-hidden="true"><Rss :size="19" /></span>
      <div class="min-w-0">
        <h2 id="rss-title" class="text-base font-bold text-ink">RSS 订阅</h2>
        <p class="mt-0.5 text-xs leading-relaxed text-muted">把站内动态放进你常用的阅读器，不需要登录。</p>
      </div>
    </header>
    <div class="mt-4 flex flex-wrap gap-2" :class="compact ? '' : 'sm:flex-nowrap'">
      <select v-model="community" aria-label="RSS 范围" class="select h-10 shrink-0">
        <option value="all">全部游戏</option>
        <option v-for="c in GAME_COMMUNITIES" :key="c.key" :value="c.key">{{ c.key === 'ai' ? 'AI 快讯' : c.label }}</option>
      </select>
      <input :value="url || path" readonly aria-label="RSS 地址" class="input mono h-10 min-w-0 flex-1 text-xs" @focus="($event.target as HTMLInputElement).select()">
    </div>
    <div class="mt-2 flex gap-4 text-[13px]">
      <button type="button" class="inline-flex h-8 items-center gap-1 font-semibold text-brand-ink" @click="copy">
        <Check v-if="copied" :size="14" /><Copy v-else :size="14" />{{ copied ? '已复制' : '复制地址' }}
      </button>
      <a :href="path" target="_blank" rel="noopener" class="inline-flex h-8 items-center text-muted hover:text-ink">在新标签打开</a>
    </div>
  </section>
</template>
