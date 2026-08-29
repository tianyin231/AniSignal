<script setup lang="ts">
// 随机壁纸背景层：流式两层渲染——32px 模糊预览立即铺底（blur 放大），压缩高清图加载后
// 覆盖其上；未开启或接口失败时不渲染（自然回退默认极光背景）。
const { status, src, preview } = useWallpaper()
const visible = computed(() => (status.value === 'loading' || status.value === 'on') && !!src.value)
</script>

<template>
  <div v-if="visible" class="pointer-events-none fixed inset-0 -z-10" aria-hidden="true">
    <div
      v-if="preview"
      class="absolute inset-0 scale-110 bg-cover bg-center bg-no-repeat blur-lg"
      :style="{ backgroundImage: `url('${preview}')` }"
    />
    <div class="absolute inset-0 bg-cover bg-center bg-no-repeat" :style="{ backgroundImage: `url('${src}')` }" />
    <!-- 半透明纱罩保证浅色/深色主题下的可读性 -->
    <div class="absolute inset-0 bg-slate-100/45 dark:bg-zinc-950/60" />
  </div>
</template>
