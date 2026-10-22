<script setup lang="ts">
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

const emit = defineEmits<{ saved: [] }>()
const selectedGames = ref<string[]>([])
const excludedText = ref('')
const ready = ref(false)
const busy = ref(false)
const message = ref('')
const error = ref('')
interface Preferences { communities: string[], exclude_keywords: string[] }

onMounted(async () => {
  try {
    const result = await $fetch<Preferences>('/api/account/feed-preferences')
    selectedGames.value = result.communities
    excludedText.value = result.exclude_keywords.join('，')
    ready.value = true
  } catch {
    error.value = '偏好加载失败，请刷新后重试。'
  }
})

async function save() {
  if (busy.value || !ready.value) return
  busy.value = true
  message.value = error.value = ''
  try {
    await $fetch('/api/account/feed-preferences', {
      method: 'PUT',
      body: {
        communities: selectedGames.value,
        exclude_keywords: [...new Set(excludedText.value.split(/[,，\n]/).map(v => v.trim()).filter(Boolean))],
      },
    })
    message.value = '已保存，关注流和之后生成的邮件会按此筛选。'
    emit('saved')
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? '保存失败，请重试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="panel p-5" aria-labelledby="preferences-title">
    <h2 id="preferences-title" class="text-sm font-semibold">个人降噪</h2>
    <p class="mt-1 text-xs leading-relaxed text-muted">用于关键词关注流、每日早报和 AI 精炼邮件。定制监控仍按各自规则收集。</p>
    <form class="mt-3 space-y-3" @submit.prevent="save">
      <fieldset :disabled="!ready || busy">
        <legend class="mb-2 text-xs text-muted">关注的游戏（未选时包含全部游戏）</legend>
        <div class="flex flex-wrap items-center gap-x-4 gap-y-3">
          <button type="button" :aria-pressed="!selectedGames.length" class="rounded border px-2.5 py-1 text-xs" :class="!selectedGames.length ? 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300' : 'border-line'" @click="selectedGames = []">全部游戏</button>
          <label v-for="game in GAME_COMMUNITIES" :key="game.key" class="inline-flex cursor-pointer items-center gap-1.5 text-sm">
            <input v-model="selectedGames" type="checkbox" :value="game.key" class="checkbox">{{ game.label }}
          </label>
        </div>
      </fieldset>
      <div>
        <label for="exclude-keywords" class="mb-1.5 block text-xs text-muted">排除标题中的词（逗号或换行分隔，最多 20 个）</label>
        <textarea id="exclude-keywords" v-model="excludedText" :disabled="!ready || busy" rows="2" maxlength="1320" placeholder="例如：二创，配队，抽卡记录" class="input w-full px-3 py-2 text-sm" />
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <button type="submit" class="btn-primary px-4 py-2 text-sm" :disabled="!ready || busy">{{ busy ? '保存中…' : '保存偏好' }}</button>
        <p v-if="message" role="status" class="text-xs text-emerald-600 dark:text-emerald-400">{{ message }}</p>
        <p v-if="error" role="alert" class="text-xs text-red-600 dark:text-red-400">{{ error }}</p>
      </div>
    </form>
  </section>
</template>
