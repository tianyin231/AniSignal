<script setup lang="ts">
import { Plus, X } from 'lucide-vue-next'
import { MAX_KEYWORDS } from '~/composables/useAccountCenter'

// 关键词：标题命中任一启用的关键词即进入关注流；点词暂停 / 启用，× 删除。
const center = useAccountCenter()
const { keywords, keywordBusy, keywordError, loading, errors } = center
const draft = ref('')
const input = ref<HTMLInputElement | null>(null)
const full = computed(() => keywords.value.length >= MAX_KEYWORDS)
const SUGGESTIONS = ['版本前瞻', '联动', '手办', '兑换码']
const suggestions = computed(() => SUGGESTIONS.filter(word => !keywords.value.some(k => k.keyword === word)))

async function add(word = draft.value.trim()) {
  if (!word || full.value) return
  if (await center.addKeyword(word)) draft.value = ''
}

defineExpose({ focus: () => input.value?.focus() })
</script>

<template>
  <section id="keyword-monitor" class="panel scroll-mt-24 p-5" aria-labelledby="keywords-title">
    <header class="flex items-baseline justify-between gap-2">
      <h2 id="keywords-title" class="text-base font-bold text-ink">关键词</h2>
      <span v-if="!loading.keywords && !errors.keywords" class="text-xs text-muted">
        <span class="num font-semibold text-ink-2">{{ keywords.length }}</span>/{{ MAX_KEYWORDS }}
      </span>
    </header>
    <p class="mt-1 text-xs leading-relaxed text-muted">标题命中任一启用的关键词，就会收进关注流和邮件。</p>

    <form class="mt-3 flex gap-2" @submit.prevent="add()">
      <div class="relative min-w-0 flex-1">
        <Plus :size="15" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden="true" />
        <input
          id="keyword-input"
          ref="input"
          v-model="draft"
          maxlength="64"
          :disabled="full"
          :placeholder="full ? `最多 ${MAX_KEYWORDS} 个关键词` : '角色、版本或商品名'"
          aria-label="添加关键词"
          class="input h-10 w-full pl-9"
        >
      </div>
      <button type="submit" class="btn-primary h-10 shrink-0 px-4" :disabled="keywordBusy || !draft.trim() || full">添加</button>
    </form>
    <UiNotice v-if="keywordError" tone="error" class="mt-3">{{ keywordError }}</UiNotice>

    <p v-if="loading.keywords" role="status" class="mt-4 text-xs text-faint">正在加载关键词…</p>
    <UiNotice v-else-if="errors.keywords" tone="warn" class="mt-3">
      {{ errors.keywords }}<button type="button" class="ml-2 font-semibold underline" @click="center.fetchKeywords()">重试</button>
    </UiNotice>
    <template v-else>
      <ul v-if="keywords.length" class="mt-4 flex flex-wrap gap-2">
        <li
          v-for="k in keywords"
          :key="k.id"
          class="flex h-8 items-center rounded-full border text-[13px] transition-colors"
          :class="k.enabled ? 'border-transparent bg-brand-soft text-brand-ink' : 'border-line text-faint'"
        >
          <button
            type="button"
            class="flex h-full items-center gap-1.5 rounded-l-full pl-3 pr-1.5"
            :disabled="keywordBusy"
            :aria-pressed="k.enabled"
            :title="k.enabled ? '点击暂停' : '点击启用'"
            @click="center.toggleKeyword(k)"
          >
            <span class="size-1.5 rounded-full" :class="k.enabled ? 'bg-brand' : 'bg-faint'" aria-hidden="true" />
            <span :class="k.enabled ? 'font-medium' : 'line-through'">{{ k.keyword }}</span>
            <span v-if="k.enabled" class="num text-[11px] opacity-75">{{ k.matches }}</span>
          </button>
          <button
            type="button"
            class="grid h-full w-7 place-items-center rounded-r-full opacity-50 transition-opacity hover:opacity-100"
            :disabled="keywordBusy"
            :aria-label="`删除关键词 ${k.keyword}`"
            @click="center.removeKeyword(k)"
          >
            <X :size="13" />
          </button>
        </li>
      </ul>
      <div v-else class="mt-4 rounded-xl border border-dashed border-line p-4 text-center">
        <p class="text-sm text-muted">还没有关键词。先加一个试试：</p>
        <div class="mt-3 flex flex-wrap justify-center gap-2">
          <button
            v-for="word in suggestions"
            :key="word"
            type="button"
            class="h-8 rounded-full bg-chip px-3 text-[13px] text-ink-2 transition-colors hover:bg-brand-soft hover:text-brand-ink"
            :disabled="keywordBusy"
            @click="add(word)"
          >
            + {{ word }}
          </button>
        </div>
      </div>
    </template>
  </section>
</template>
