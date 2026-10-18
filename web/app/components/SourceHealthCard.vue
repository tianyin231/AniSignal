<script setup lang="ts">
import type { PublicSource } from '~/composables/usePublicApi'

// 来源健康点阵：每个启用中的采集来源一格，连续失败的标橙并排在最后
const props = defineProps<{ sources: PublicSource[] }>()

const enabled = computed(() => props.sources.filter(s => s.enabled))
const failing = computed(() => enabled.value.filter(s => s.consecutive_failures > 0))
const cells = computed(() =>
  [...enabled.value].sort((a, b) => Number(a.consecutive_failures > 0) - Number(b.consecutive_failures > 0)),
)
const okCount = computed(() => enabled.value.length - failing.value.length)
</script>

<template>
  <section v-if="enabled.length" class="panel p-5" aria-labelledby="source-health-title">
    <header class="flex items-baseline justify-between">
      <h2 id="source-health-title" class="text-base font-bold text-ink">来源健康</h2>
      <span class="num text-[15px] font-bold text-ink">{{ okCount }}<span class="font-medium text-faint">/{{ enabled.length }}</span></span>
    </header>
    <div
      class="mt-3.5 grid grid-cols-[repeat(15,minmax(0,1fr))] gap-[5px]"
      role="img"
      :aria-label="`${enabled.length} 个来源中 ${okCount} 个正常，${failing.length} 个连续采集失败`"
    >
      <span
        v-for="s in cells"
        :key="`${s.community}-${s.name}`"
        class="aspect-square rounded-[3px]"
        :class="s.consecutive_failures > 0 ? 'bg-warn' : 'bg-ok/75'"
        :title="s.consecutive_failures > 0 ? `${s.name} · 连续失败 ${s.consecutive_failures} 次` : s.name"
      />
    </div>
    <NuxtLink to="/sources" class="link mt-3 inline-block text-[13px]">
      {{ failing.length ? `${failing.length} 个来源连续采集失败` : '全部来源' }}
    </NuxtLink>
  </section>
</template>
