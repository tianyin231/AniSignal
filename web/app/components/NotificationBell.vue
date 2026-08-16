<script setup lang="ts">
import { Bell } from 'lucide-vue-next'
import { deliveries } from '~/utils/data'

const open = ref(false)
const route = useRoute()
watch(
  () => route.fullPath,
  () => {
    open.value = false
  },
)

const recent = deliveries.slice(0, 3)
const unread = deliveries.filter((d) => d.unread).length
</script>

<template>
  <div class="relative">
    <button
      type="button"
      aria-label="通知"
      class="relative grid size-8 place-items-center rounded-md text-slate-500 transition-colors hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
      @click="open = !open"
    >
      <Bell :size="16" />
      <span v-if="unread" class="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-rose-500" />
    </button>

    <div v-if="open" class="fixed inset-0 z-40" @click="open = false" />
    <div v-if="open" class="popover absolute right-0 top-[calc(100%+8px)] z-50 w-80 p-1.5">
      <div class="flex items-center justify-between px-2.5 py-2">
        <p class="text-sm font-semibold text-slate-800 dark:text-zinc-100">通知</p>
        <span class="mono text-xs text-slate-400 dark:text-zinc-500">{{ unread }} 条未读</span>
      </div>
      <div class="my-1 border-t border-slate-200/70 dark:border-zinc-700/60" />
      <ul>
        <li v-for="d in recent" :key="d.at" class="px-2.5 py-2">
          <div class="flex items-center gap-2">
            <span
              class="chip w-11 shrink-0 justify-center"
              :class="d.type === '即时' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' : 'bg-slate-200/70 text-slate-600 dark:bg-zinc-800 dark:text-zinc-300'"
            >
              {{ d.type }}
            </span>
            <NuxtLink
              v-if="d.eventId"
              :to="`/events/${d.eventId}`"
              class="min-w-0 flex-1 truncate text-[13px] text-slate-700 transition-colors hover:text-blue-700 dark:text-zinc-200 dark:hover:text-blue-300"
            >
              {{ d.event }}
            </NuxtLink>
            <span v-else class="min-w-0 flex-1 truncate text-[13px] text-slate-700 dark:text-zinc-200">{{ d.event }}</span>
            <span v-if="d.unread" class="size-1.5 shrink-0 rounded-full bg-rose-500" />
          </div>
          <p class="mono mt-0.5 pl-[52px] text-xs text-slate-400 dark:text-zinc-500">{{ d.at }} · {{ d.rule }}</p>
        </li>
      </ul>
      <div class="my-1 border-t border-slate-200/70 dark:border-zinc-700/60" />
      <NuxtLink
        to="/watch"
        class="block rounded-md px-2.5 py-1.5 text-center text-xs text-slate-500 transition-colors hover:bg-slate-200/60 hover:text-slate-800 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-200"
      >
        全部发送记录 →
      </NuxtLink>
    </div>
  </div>
</template>
