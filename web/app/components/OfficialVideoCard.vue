<script setup lang="ts">
import { Images, Play } from 'lucide-vue-next'
import { platformMeta, type RawItem } from '~/utils/data'

const props = defineProps<{ item: RawItem }>()
const coverFailed = ref(false)
const platform = computed(() => platformMeta[props.item.platform])
const isVideo = computed(() => props.item.platform === 'youtube')

watch(() => props.item.cover, () => (coverFailed.value = false))
</script>

<template>
  <NuxtLink
    :to="`/contents/${item.id}`"
    class="group cursor-pointer overflow-hidden rounded-xl border border-slate-900/10 bg-white/60 transition-colors hover:border-blue-400/60 hover:bg-white/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-white/10 dark:bg-white/5 dark:hover:border-blue-400/50 dark:hover:bg-white/[0.08]"
  >
    <div class="relative aspect-video overflow-hidden bg-slate-100 dark:bg-zinc-900">
      <img
        v-if="item.cover && !coverFailed"
        :src="item.cover"
        :alt="`${item.title} 封面`"
        referrerpolicy="no-referrer"
        loading="lazy"
        class="size-full object-cover transition-opacity duration-200 group-hover:opacity-90"
        @error="coverFailed = true"
      >
      <div v-else class="grid size-full place-items-center text-slate-300 dark:text-zinc-700">
        <Play :size="28" aria-hidden="true" />
      </div>

      <span class="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-black/75 px-2 py-1 text-[11px] font-medium text-white backdrop-blur-sm">
        <span class="grid size-4 place-items-center rounded-full text-[8px] font-bold text-white" :class="platform.badge">
          {{ platform.short }}
        </span>
        {{ platform.label }}
      </span>
      <span class="absolute bottom-2 right-2 grid size-8 place-items-center rounded-full bg-black/70 text-white backdrop-blur-sm transition-colors group-hover:bg-blue-600">
        <Play v-if="isVideo" :size="14" fill="currentColor" aria-hidden="true" />
        <Images v-else :size="14" aria-hidden="true" />
      </span>
    </div>

    <div class="p-3">
      <h3 class="line-clamp-2 min-h-10 text-sm font-medium leading-5 text-slate-800 transition-colors group-hover:text-blue-700 dark:text-zinc-100 dark:group-hover:text-blue-300">
        {{ item.title }}
      </h3>
      <p class="mt-1.5 truncate text-xs text-slate-500 dark:text-zinc-400">
        {{ item.account }} · {{ item.publishedAt }}
      </p>
    </div>
  </NuxtLink>
</template>
