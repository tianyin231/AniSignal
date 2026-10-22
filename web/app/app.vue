<script setup lang="ts">
import type { PublicSiteConfig } from '~/composables/useSiteConfig'

const { api } = usePublicApi()
useAsyncData('site-config', (_app, { signal }) => api<PublicSiteConfig>('/api/site-config', { signal }))
</script>

<template>
  <NuxtLoadingIndicator color="#3b82f6" :height="2" />
  <!-- 壁纸与材质状态属于应用，切换布局时不能卸载重建。 -->
  <WallpaperBackdrop />
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
