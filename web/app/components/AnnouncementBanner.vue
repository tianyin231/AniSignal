<script setup lang="ts">
import { Megaphone, X } from 'lucide-vue-next'

// 全服公告横幅（管理端配置；本次会话可关闭）
const { announcement } = useSiteConfig()
const dismissed = ref(false)
const visible = computed(() => announcement.value.enabled && announcement.value.text && !dismissed.value)
</script>

<template>
  <div
    v-if="visible"
    class="flex items-center gap-2 px-4 py-2 text-xs sm:text-[13px]"
    :class="announcement.level === 'warning'
      ? 'bg-amber-500/15 text-amber-800 dark:text-amber-200'
      : 'bg-blue-500/10 text-blue-800 dark:text-blue-200'"
  >
    <Megaphone :size="14" class="shrink-0" />
    <NuxtLink v-if="announcement.link" :to="announcement.link" class="min-w-0 flex-1 leading-relaxed hover:underline">
      {{ announcement.text }}
    </NuxtLink>
    <p v-else class="min-w-0 flex-1 leading-relaxed">{{ announcement.text }}</p>
    <button class="shrink-0 rounded p-0.5 opacity-60 transition-opacity hover:opacity-100" title="关闭" @click="dismissed = true">
      <X :size="14" />
    </button>
  </div>
</template>
