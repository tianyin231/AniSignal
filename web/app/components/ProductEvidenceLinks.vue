<script setup lang="ts">
import type { ApiProductEvidence } from '~/composables/usePublicApi'

defineProps<{
  items: ApiProductEvidence[]
}>()
</script>

<template>
  <details v-if="items.length" class="group mt-2 text-xs">
    <summary class="cursor-pointer select-none text-[11px] text-blue-600 marker:text-slate-300 hover:text-blue-700 dark:text-blue-300 dark:hover:text-blue-200">
      查看判断证据（{{ items.length }}）
    </summary>
    <ul class="mt-1.5 space-y-1">
      <li
        v-for="item in items"
        :key="item.content_id"
        class="flex items-center gap-2 rounded-md bg-slate-500/5 px-2 py-1.5 dark:bg-white/5"
      >
        <NuxtLink
          :to="`/contents/${item.content_id}`"
          class="min-w-0 flex-1 truncate text-slate-600 hover:text-blue-600 dark:text-zinc-300 dark:hover:text-blue-300"
          :title="item.title || `内容 #${item.content_id}`"
        >
          {{ item.title || `内容 #${item.content_id}` }}
        </NuxtLink>
        <a
          v-if="item.url"
          :href="item.url"
          target="_blank"
          rel="noopener"
          class="shrink-0 text-[11px] text-slate-400 hover:text-blue-600 dark:hover:text-blue-300"
          :aria-label="`查看${item.title || `内容 #${item.content_id}`}原文`"
        >原文 ↗</a>
      </li>
    </ul>
  </details>
</template>
