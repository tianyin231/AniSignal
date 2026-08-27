<script setup lang="ts">
import { ArrowRight, ImageIcon, ShieldCheck } from 'lucide-vue-next'
import { platformMeta, type RawItem } from '~/utils/data'

const props = defineProps<{ item: RawItem }>()
const coverFailed = ref(false)
const platform = computed(() => platformMeta[props.item.platform])

watch(() => props.item.cover, () => (coverFailed.value = false))
</script>

<template>
  <article class="panel overflow-hidden">
    <div class="grid md:grid-cols-[minmax(0,1.45fr)_minmax(15rem,0.85fr)]">
      <NuxtLink
        :to="`/contents/${item.id}`"
        class="group relative block min-h-56 cursor-pointer overflow-hidden bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 md:min-h-72 dark:bg-zinc-900"
      >
        <img
          v-if="item.cover && !coverFailed"
          :src="item.cover"
          :alt="`${item.title} 官方内容封面`"
          referrerpolicy="no-referrer"
          loading="eager"
          fetchpriority="high"
          class="absolute inset-0 size-full object-cover transition-opacity duration-200 group-hover:opacity-95"
          @error="coverFailed = true"
        >
        <div v-else class="absolute inset-0 grid place-items-center bg-gradient-to-br from-blue-100 via-sky-50 to-cyan-100 text-blue-300 dark:from-blue-950 dark:via-zinc-900 dark:to-cyan-950 dark:text-blue-700">
          <ImageIcon :size="52" aria-hidden="true" />
        </div>
        <div class="absolute inset-x-0 bottom-0 h-28 bg-gradient-to-t from-black/65 to-transparent" aria-hidden="true" />
        <span class="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-blue-600/95 px-2.5 py-1 text-xs font-semibold text-white shadow-sm backdrop-blur-sm">
          <ShieldCheck :size="14" aria-hidden="true" />
          官方发布
        </span>
        <span class="absolute bottom-3 left-3 rounded-full bg-black/70 px-2.5 py-1 text-xs text-white backdrop-blur-sm">
          {{ platform.label }}
        </span>
      </NuxtLink>

      <div class="flex min-w-0 flex-col p-4 sm:p-5">
        <p class="text-xs font-semibold tracking-wide text-blue-700 dark:text-blue-300">本周官方精选</p>
        <NuxtLink
          :to="`/contents/${item.id}`"
          class="mt-2 rounded-sm text-xl font-semibold leading-snug text-slate-900 transition-colors hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-zinc-50 dark:hover:text-blue-300"
        >
          {{ item.title }}
        </NuxtLink>
        <p v-if="item.excerpt" class="mt-3 line-clamp-4 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
          {{ item.excerpt }}
        </p>
        <div v-if="item.keywords?.length" class="mt-3 flex flex-wrap gap-1.5">
          <span v-for="keyword in item.keywords" :key="keyword" class="rounded bg-blue-500/10 px-2 py-1 text-[11px] text-blue-700 dark:text-blue-300">
            {{ keyword }}
          </span>
        </div>
        <div class="mt-auto flex items-end justify-between gap-4 pt-5">
          <p class="min-w-0 text-xs text-slate-500 dark:text-zinc-400">
            <span class="block truncate">{{ item.account }}</span>
            <span class="mono mt-0.5 block text-slate-400 dark:text-zinc-500">{{ item.publishedAt }}</span>
          </p>
          <NuxtLink
            :to="`/contents/${item.id}`"
            class="inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-zinc-900"
          >
            查看详情
            <ArrowRight :size="15" aria-hidden="true" />
          </NuxtLink>
        </div>
      </div>
    </div>
  </article>
</template>
