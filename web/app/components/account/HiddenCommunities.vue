<script setup lang="ts">
import { EyeOff } from 'lucide-vue-next'
import { communityClasses, HIDDEN_COMMUNITIES_COOKIE } from '~/composables/useGameSection'
import { apiErrorDetail } from '~/utils/api-errors'
import { platformMeta, type Platform } from '~/utils/data'

// 网站浏览屏蔽：只影响本账号在网站上看到的列表与顶栏，不改邮件、Bot 推送和关注流。
// 来源平台按游戏分区与 AI 快讯分开设置，令牌形如 game:bilibili / ai:x，与后端 BLOCKABLE_PLATFORMS 对应。
interface Blocks { communities: string[], platforms: string[] }

const { communities: activeCommunities } = useGameSection()
const games = computed(() =>
  activeCommunities.value.map(game => ({ ...game, label: game.key === 'ai' ? 'AI 快讯' : game.label, dot: communityClasses(game.color).dot })),
)
const PLATFORM_GROUPS = [
  { scope: 'game', label: '游戏分区', keys: ['bilibili', 'youtube', 'x', 'kuro', 'miyoushe', 'game_official'] },
  { scope: 'ai', label: 'AI 快讯', keys: ['bilibili', 'x', 'rss'] },
] as const
const platformGroups = PLATFORM_GROUPS.map(group => ({
  ...group,
  chips: group.keys.map(key => ({
    token: `${group.scope}:${key}`,
    label: platformMeta[key as Platform]?.label ?? key,
    dot: platformMeta[key as Platform]?.badge ?? 'bg-line',
  })),
}))

const communities = ref<string[]>([])
const platforms = ref<string[]>([])
const saved = ref('')
const ready = ref(false)
const busy = ref(false)
const message = ref('')
const error = ref('')

const snapshot = () => JSON.stringify([[...communities.value].sort(), [...platforms.value].sort()])
const dirty = computed(() => ready.value && snapshot() !== saved.value)
const allHidden = computed(() => games.value.length > 0 && communities.value.length >= games.value.length)

function apply(result: Blocks) {
  communities.value = result.communities
  platforms.value = result.platforms ?? []
  saved.value = snapshot()
}

onMounted(async () => {
  try {
    apply(await $fetch<Blocks>('/api/account/hidden-communities'))
    ready.value = true
  } catch {
    error.value = '屏蔽设置加载失败，请刷新后重试。'
  }
})

const flip = (list: string[], key: string) => list.includes(key) ? list.filter(k => k !== key) : [...list, key]
function toggleCommunity(key: string) {
  communities.value = flip(communities.value, key)
  message.value = ''
}
function togglePlatform(token: string) {
  platforms.value = flip(platforms.value, token)
  message.value = ''
}

async function save() {
  if (busy.value || !ready.value || allHidden.value) return
  busy.value = true
  message.value = error.value = ''
  try {
    const result = await $fetch<Blocks>('/api/account/hidden-communities', {
      method: 'PUT',
      body: { communities: communities.value, platforms: platforms.value },
    })
    apply(result)
    // 后端已更新镜像 Cookie：重新读取让顶栏立即变化，并丢弃按旧设置加载的列表。
    refreshCookie(HIDDEN_COMMUNITIES_COOKIE)
    clearNuxtData()
    message.value = result.communities.length || result.platforms.length ? '已保存，网站列表会按此过滤。' : '已保存，所有内容都会显示。'
  } catch (exc) {
    error.value = apiErrorDetail(exc, '保存失败，请重试。')
  } finally {
    busy.value = false
  }
}

const chipClass = (on: boolean) => on ? 'border-ink/70 bg-chip font-semibold text-ink' : 'border-line text-ink-2 hover:text-ink'
</script>

<template>
  <section id="hidden-communities" class="panel p-5" aria-labelledby="hidden-communities-title">
    <h2 id="hidden-communities-title" class="text-base font-bold text-ink">屏蔽分区与来源</h2>
    <p class="mt-1 text-xs leading-relaxed text-muted">
      只影响登录本账号时在网站上看到的列表，不影响邮件、Bot 推送和其他账号。
    </p>

    <form class="mt-4 space-y-5" @submit.prevent="save">
      <fieldset :disabled="!ready || busy">
        <legend class="text-xs font-medium text-ink-2">游戏分区</legend>
        <p class="mt-0.5 text-xs leading-relaxed text-faint">首页、内容库、搜索、日历、兑换码、事件和商品的「全部」视图不再显示，顶栏也会隐藏；直接打开该分区仍可查看。</p>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="game in games"
            :key="game.key"
            type="button"
            class="flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors"
            :class="chipClass(communities.includes(game.key))"
            :aria-pressed="communities.includes(game.key)"
            :aria-label="`${communities.includes(game.key) ? '已屏蔽' : '屏蔽'}：${game.label}`"
            @click="toggleCommunity(game.key)"
          >
            <EyeOff v-if="communities.includes(game.key)" :size="13" aria-hidden="true" />
            <span v-else class="size-[7px] rounded-full" :class="game.dot" aria-hidden="true" />
            <span :class="communities.includes(game.key) ? 'line-through decoration-ink/40' : ''">{{ game.label }}</span>
          </button>
        </div>
      </fieldset>

      <fieldset :disabled="!ready || busy">
        <legend class="text-xs font-medium text-ink-2">来源平台</legend>
        <p class="mt-0.5 text-xs leading-relaxed text-faint">游戏分区和 AI 快讯分开设置，所选平台的内容在各列表里都不再出现；内容库里点选该平台时仍可查看。AI 日报、周报不受影响。</p>
        <div v-for="group in platformGroups" :key="group.scope" class="mt-2.5 sm:flex sm:items-start sm:gap-3">
          <p class="text-xs text-muted sm:w-16 sm:shrink-0 sm:pt-[7px]">{{ group.label }}</p>
          <div class="mt-1.5 flex flex-wrap gap-1.5 sm:mt-0">
            <button
              v-for="chip in group.chips"
              :key="chip.token"
              type="button"
              class="flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors"
              :class="chipClass(platforms.includes(chip.token))"
              :aria-pressed="platforms.includes(chip.token)"
              :aria-label="`${platforms.includes(chip.token) ? '已屏蔽' : '屏蔽'}：${group.label}的${chip.label}`"
              @click="togglePlatform(chip.token)"
            >
              <EyeOff v-if="platforms.includes(chip.token)" :size="13" aria-hidden="true" />
              <span v-else class="size-[7px] rounded-full" :class="chip.dot" aria-hidden="true" />
              <span :class="platforms.includes(chip.token) ? 'line-through decoration-ink/40' : ''">{{ chip.label }}</span>
            </button>
          </div>
        </div>
      </fieldset>

      <div class="flex flex-wrap items-center gap-3">
        <button type="submit" class="btn-primary h-9 px-4" :disabled="!dirty || busy || allHidden">{{ busy ? '保存中…' : '保存' }}</button>
        <span v-if="allHidden" class="text-xs text-hot">至少保留一个分区。</span>
        <span v-else-if="message" role="status" class="text-xs text-ok">{{ message }}</span>
        <span v-else-if="dirty" class="text-xs text-faint">有未保存的修改</span>
        <span v-else-if="ready && !communities.length && !platforms.length" class="text-xs text-faint">当前没有屏蔽任何内容。</span>
      </div>
      <UiNotice v-if="error" tone="error" class="mt-3">{{ error }}</UiNotice>
    </form>
  </section>
</template>
