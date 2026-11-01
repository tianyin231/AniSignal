<script setup lang="ts">
import { communityClasses, GAME_COMMUNITIES } from '~/composables/useGameSection'
import { apiErrorDetail } from '~/utils/api-errors'

// 个人降噪：限定游戏 + 排除词，作用于关注流、每日早报和 AI 精炼邮件。
const emit = defineEmits<{ saved: [] }>()
interface Preferences { communities: string[], exclude_keywords: string[] }
const games = GAME_COMMUNITIES.map(game => ({ ...game, label: game.key === 'ai' ? 'AI 快讯' : game.label, dot: communityClasses(game.color).dot }))
const selected = ref<string[]>([])
const excludedText = ref('')
const saved = ref('')
const ready = ref(false)
const busy = ref(false)
const message = ref('')
const error = ref('')

const words = (value: string) => [...new Set(value.split(/[,，\n]/).map(v => v.trim()).filter(Boolean))]
const snapshot = () => JSON.stringify([[...selected.value].sort(), words(excludedText.value)])
const dirty = computed(() => ready.value && snapshot() !== saved.value)

onMounted(async () => {
  try {
    const result = await $fetch<Preferences>('/api/account/feed-preferences')
    selected.value = result.communities
    excludedText.value = result.exclude_keywords.join('，')
    saved.value = snapshot()
    ready.value = true
  } catch {
    error.value = '偏好加载失败，请刷新后重试。'
  }
})

function toggle(key: string) {
  selected.value = selected.value.includes(key) ? selected.value.filter(k => k !== key) : [...selected.value, key]
  message.value = ''
}

async function save() {
  if (busy.value || !ready.value) return
  busy.value = true
  message.value = error.value = ''
  try {
    await $fetch('/api/account/feed-preferences', {
      method: 'PUT',
      body: { communities: selected.value, exclude_keywords: words(excludedText.value) },
    })
    saved.value = snapshot()
    message.value = '已保存，关注流和之后的邮件会按此筛选。'
    emit('saved')
  } catch (exc) {
    error.value = apiErrorDetail(exc, '保存失败，请重试。')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="panel p-5" aria-labelledby="preferences-title">
    <h2 id="preferences-title" class="text-base font-bold text-ink">个人降噪</h2>
    <p class="mt-1 text-xs leading-relaxed text-muted">作用于关注流、每日早报和 AI 精炼邮件；定制监控按各自规则收集。</p>

    <form class="mt-4" @submit.prevent="save">
      <fieldset :disabled="!ready || busy">
        <legend class="text-xs font-medium text-ink-2">只看这些游戏 <span class="font-normal text-faint">（不选＝全部）</span></legend>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="game in games"
            :key="game.key"
            type="button"
            class="flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors"
            :class="selected.includes(game.key) ? 'border-ink/70 bg-card font-semibold text-ink' : 'border-line text-muted hover:text-ink'"
            :aria-pressed="selected.includes(game.key)"
            @click="toggle(game.key)"
          >
            <span class="size-[7px] rounded-full" :class="selected.includes(game.key) ? game.dot : 'bg-line'" aria-hidden="true" />
            {{ game.label }}
          </button>
        </div>
      </fieldset>

      <label for="exclude-keywords" class="mt-4 block text-xs font-medium text-ink-2">排除标题含这些词的内容</label>
      <textarea
        id="exclude-keywords"
        v-model="excludedText"
        :disabled="!ready || busy"
        rows="2"
        maxlength="1320"
        placeholder="逗号或换行分隔，最多 20 个，例如：二创，配队，抽卡记录"
        class="textarea mt-1.5 w-full"
        @input="message = ''"
      />

      <div class="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" class="btn-primary h-9 px-4" :disabled="!dirty || busy">{{ busy ? '保存中…' : '保存' }}</button>
        <span v-if="message" role="status" class="text-xs text-ok">{{ message }}</span>
        <span v-else-if="dirty" class="text-xs text-faint">有未保存的修改</span>
      </div>
      <UiNotice v-if="error" tone="error" class="mt-3">{{ error }}</UiNotice>
    </form>
  </section>
</template>
