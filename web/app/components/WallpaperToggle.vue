<script setup lang="ts">
import { Download, Image as ImageIcon, ImageOff, Loader2 } from 'lucide-vue-next'

// 壁纸开关：与浅色/深色主题同级的用户偏好。开启后旁边出现当前背景原图下载按钮。
const { status, enable, disable } = useWallpaper()

const title = computed(() => ({
  off: '开启随机壁纸背景',
  loading: '壁纸加载中…',
  on: '关闭随机壁纸背景',
  failed: '壁纸加载失败，已回退默认背景，点击重试',
})[status.value])

function onToggle() {
  if (status.value === 'on') disable()
  else if (status.value === 'off' || status.value === 'failed') enable()
}
</script>

<template>
  <div class="flex items-center gap-0.5">
    <button
      type="button"
      :aria-label="title"
      :title="title"
      :disabled="status === 'loading'"
      class="grid size-8 place-items-center rounded-md text-slate-500 transition-colors hover:bg-slate-200/70 hover:text-slate-900 disabled:cursor-wait dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
      @click="onToggle"
    >
      <Loader2 v-if="status === 'loading'" :size="16" class="animate-spin" />
      <ImageIcon v-else-if="status === 'on'" :size="16" class="text-blue-600 dark:text-blue-300" />
      <ImageOff v-else :size="16" :class="status === 'failed' ? 'text-amber-500' : ''" />
    </button>
    <a
      v-if="status === 'on'"
      href="/api/wallpaper/download"
      :download="`anisignal-wallpaper.jpg`"
      title="下载当前背景原图"
      aria-label="下载当前背景原图"
      class="grid size-8 place-items-center rounded-md text-slate-500 transition-colors hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
    >
      <Download :size="15" />
    </a>
  </div>
</template>
