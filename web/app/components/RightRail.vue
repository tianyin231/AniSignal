<template>
  <div class="space-y-4">
    <StatusCard />
    <section v-if="scopedCommunity" class="panel p-5" aria-label="官方链接">
      <OfficialLinks />
    </section>
    <SourceHealthMini />
    <SubscribeCta />
  </div>
</template>

<script setup lang="ts">
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

const route = useRoute()
// 官方外链只在明确的游戏分区视角下展示（聚合视角下无从判断该给哪款游戏的链接）
const scopedCommunity = computed(() => GAME_COMMUNITIES.some(c => c.key === route.query.community && c.key !== 'ai'))
</script>
