<script setup lang="ts">
// 随机壁纸背景层：图床 CDN 直连图固定在页面最底层，盖过极光渐变、承托玻璃面板；
// 未开启或加载失败时不渲染（自然回退默认背景）。
const { status, src } = useWallpaper()
</script>

<template>
  <div v-if="status === 'on' && src" class="pointer-events-none fixed inset-0 -z-10" aria-hidden="true">
    <div class="absolute inset-0 bg-cover bg-center bg-no-repeat" :style="{ backgroundImage: `url('${src}')` }" />
    <!-- 半透明纱罩保证浅色/深色主题下的可读性 -->
    <div class="absolute inset-0 bg-slate-100/45 dark:bg-zinc-950/60" />
  </div>
</template>
