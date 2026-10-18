<script setup lang="ts">
// 随机壁纸背景层：图床 CDN 直连图固定在页面最底层，承托玻璃面板；
// 未开启或加载失败时不渲染（自然回退默认底色）。
// 开启时给 <html> 挂 data-wallpaper：main.css 据此把实底卡片切成毛玻璃，让壁纸透出来。
const { status, src } = useWallpaper()
const active = computed(() => status.value === 'on' && !!src.value)

if (import.meta.client) {
  watch(active, (on) => document.documentElement.toggleAttribute('data-wallpaper', on), { immediate: true })
  onBeforeUnmount(() => document.documentElement.removeAttribute('data-wallpaper'))
}
</script>

<template>
  <div v-if="active" class="pointer-events-none fixed inset-0 -z-10" aria-hidden="true">
    <div class="absolute inset-0 bg-cover bg-center bg-no-repeat" :style="{ backgroundImage: `url('${src}')` }" />
    <!-- 半透明纱罩保证浅色/深色主题下的可读性 -->
    <div class="absolute inset-0 bg-canvas/45 dark:bg-canvas/60" />
  </div>
</template>
