<script setup lang="ts">
import { EyeOff } from 'lucide-vue-next'
import { communityClasses, GAME_COMMUNITIES, HIDDEN_COMMUNITIES_COOKIE } from '~/composables/useGameSection'
import { apiErrorDetail } from '~/utils/api-errors'

// 网站浏览屏蔽：只影响本账号在网站上看到的跨分区列表与顶栏，不改邮件、Bot 推送和关注流。
const games = GAME_COMMUNITIES.map(game => ({ ...game, label: game.key === 'ai' ? 'AI 快讯' : game.label, dot: communityClasses(game.color).dot }))
const selected = ref<string[]>([])
const saved = ref('')
const ready = ref(false)
const busy = ref(false)
const message = ref('')
const error = ref('')

const snapshot = () => JSON.stringify([...selected.value].sort())
const dirty = computed(() => ready.value && snapshot() !== saved.value)
const allHidden = computed(() => selected.value.length >= games.length)

onMounted(async () => {
  try {
    selected.value = (await $fetch<{ communities: string[] }>('/api/account/hidden-communities')).communities
    saved.value = snapshot()
    ready.value = true
  } catch {
    error.value = '屏蔽设置加载失败，请刷新后重试。'
  }
})

function toggle(key: string) {
  selected.value = selected.value.includes(key) ? selected.value.filter(k => k !== key) : [...selected.value, key]
  message.value = ''
}

async function save() {
  if (busy.value || !ready.value || allHidden.value) return
  busy.value = true
  message.value = error.value = ''
  try {
    const result = await $fetch<{ communities: string[] }>('/api/account/hidden-communities', {
      method: 'PUT',
      body: { communities: selected.value },
    })
    selected.value = result.communities
    saved.value = snapshot()
    // 后端已更新镜像 Cookie：重新读取让顶栏立即变化，并丢弃按旧设置加载的列表。
    refreshCookie(HIDDEN_COMMUNITIES_COOKIE)
    clearNuxtData()
    message.value = result.communities.length ? '已保存，网站列表不再显示这些分区。' : '已保存，所有分区都会显示。'
  } catch (exc) {
    error.value = apiErrorDetail(exc, '保存失败，请重试。')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section id="hidden-communities" class="panel p-5" aria-labelledby="hidden-communities-title">
    <h2 id="hidden-communities-title" class="text-base font-bold text-ink">屏蔽游戏分区</h2>
    <p class="mt-1 text-xs leading-relaxed text-muted">
      登录本账号浏览网站时，首页、内容库、搜索、日历、兑换码、事件和商品的「全部」视图不再显示所选分区，顶栏也会隐藏它们；直接打开该分区仍可查看。不影响邮件、Bot 推送和其他账号。
    </p>

    <form class="mt-4" @submit.prevent="save">
      <fieldset :disabled="!ready || busy">
        <legend class="text-xs font-medium text-ink-2">点选要屏蔽的分区</legend>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="game in games"
            :key="game.key"
            type="button"
            class="flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors"
            :class="selected.includes(game.key) ? 'border-ink/70 bg-chip font-semibold text-ink' : 'border-line text-ink-2 hover:text-ink'"
            :aria-pressed="selected.includes(game.key)"
            :aria-label="`${selected.includes(game.key) ? '已屏蔽' : '屏蔽'}：${game.label}`"
            @click="toggle(game.key)"
          >
            <EyeOff v-if="selected.includes(game.key)" :size="13" aria-hidden="true" />
            <span v-else class="size-[7px] rounded-full" :class="game.dot" aria-hidden="true" />
            <span :class="selected.includes(game.key) ? 'line-through decoration-ink/40' : ''">{{ game.label }}</span>
          </button>
        </div>
      </fieldset>

      <div class="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" class="btn-primary h-9 px-4" :disabled="!dirty || busy || allHidden">{{ busy ? '保存中…' : '保存' }}</button>
        <span v-if="allHidden" class="text-xs text-hot">至少保留一个分区。</span>
        <span v-else-if="message" role="status" class="text-xs text-ok">{{ message }}</span>
        <span v-else-if="dirty" class="text-xs text-faint">有未保存的修改</span>
        <span v-else-if="ready && !selected.length" class="text-xs text-faint">当前没有屏蔽任何分区。</span>
      </div>
      <UiNotice v-if="error" tone="error" class="mt-3">{{ error }}</UiNotice>
    </form>
  </section>
</template>
