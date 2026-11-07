<script setup lang="ts">
import type { PublicSiteConfig } from '~/composables/useSiteConfig'

const { api } = usePublicApi()
useAsyncData('site-config', (_app, { signal }) => api<PublicSiteConfig>('/api/site-config', { signal }))

const route = useRoute()
const { siteUrl } = useRuntimeConfig().public
// 规范地址保留分区；页码、筛选和搜索词都归并到同一页
const canonical = computed(() => {
  const community = route.query.community
  return `${siteUrl}${route.path}${typeof community === 'string' && community ? `?community=${encodeURIComponent(community)}` : ''}`
})
useHead({
  titleTemplate: title => !title ? 'AniSignal · 让重要的变化，找到你' : title.includes('AniSignal') ? title : `${title} · AniSignal`,
  link: [{ rel: 'canonical', href: canonical }],
})
useSeoMeta({
  ogSiteName: 'AniSignal',
  ogType: 'website',
  ogUrl: canonical,
  ogImage: `${siteUrl}/og.jpg`,
  ogImageWidth: 1200,
  ogImageHeight: 630,
  twitterCard: 'summary_large_image',
})
</script>

<template>
  <NuxtLoadingIndicator color="#3b82f6" :height="2" />
  <!-- 壁纸与材质状态属于应用，切换布局时不能卸载重建。 -->
  <WallpaperBackdrop />
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
