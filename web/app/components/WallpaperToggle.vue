<script setup lang="ts">
import { Download, ImageOff, Loader2, Wallpaper as WallpaperIcon } from 'lucide-vue-next'
import type { Component } from 'vue'

// 壁纸开关：与浅色/深色主题同级的用户偏好。开启后旁边出现当前背景原图下载按钮。
// 按钮以 status 为 key 强制重挂载，确保每次状态切换都重绘图标。
const { status, src, enable, disable } = useWallpaper()

const title = computed(() => ({
  off: '开启随机壁纸背景',
  loading: '壁纸加载中…',
  on: '关闭随机壁纸背景',
  failed: '壁纸加载失败，已回退默认背景，点击重试',
})[status.value])

const icon = computed<{ comp: Component; class: string }>(() => ({
  off: { comp: ImageOff, class: '' },
  loading: { comp: Loader2, class: 'animate-spin' },
  on: { comp: WallpaperIcon, class: 'text-brand-ink' },
  failed: { comp: ImageOff, class: 'text-amber-500' },
})[status.value]!)

const downloadHref = computed(() =>
  src.value ? `/api/wallpaper/download?url=${encodeURIComponent(src.value)}` : '',
)

function onToggle() {
  if (status.value === 'on') disable()
  else if (status.value === 'off' || status.value === 'failed') enable()
}
</script>

<template>
  <div class="flex items-center gap-0.5">
    <button
      :key="status"
      type="button"
      :aria-label="title"
      :title="title"
      :disabled="status === 'loading'"
      class="grid size-10 place-items-center rounded-[10px] text-muted transition-colors hover:bg-chip hover:text-ink disabled:cursor-wait"
      @click="onToggle"
    >
      <component :is="icon.comp" :size="18" :class="icon.class" />
    </button>
    <a
      v-if="status === 'on'"
      :key="src"
      :href="downloadHref"
      download="anisignal-wallpaper"
      title="下载当前背景原图"
      aria-label="下载当前背景原图"
      class="grid size-10 place-items-center rounded-[10px] text-muted transition-colors hover:bg-chip hover:text-ink"
    >
      <Download :size="17" />
    </a>
  </div>
</template>
